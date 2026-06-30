"""Second-round review: coverage for the confidence math, the positive entity-merge
branch, honest-null normalization, validation, the LLM enrichment wiring, and
input-order determinism — the pieces previously exercised only indirectly.
"""

import glob
import json
import os
from pathlib import Path

import pytest

from eightfold.confidence import method_factor, priority, score_field, score_overall, source_trust
from eightfold.detect import build_source, detect_kind
from eightfold.models import CanonicalProfile, Claim, FieldSpec, OutputConfig, SourceRecord
from eightfold.normalize import apply_normalizer, normalize_claim, normalize_records
from eightfold.normalize.skills import to_canonical
from eightfold.pipeline import _llm_enrich, run
from eightfold.project import project, resolve_path
from eightfold.resolve import resolve
from eightfold.sources.recruiter_csv import RecruiterCSVSource
from eightfold.validate import SchemaValidationError, _type_ok, validate_output

ROOT = Path(__file__).resolve().parent.parent
INPUTS = [f for f in glob.glob(str(ROOT / "samples/inputs/**/*"), recursive=True) if os.path.isfile(f)]


def _c(field, value, source="recruiter_csv", method="csv_direct", conf=1.0, ok=True):
    return Claim(field=field, value=value, source=source, method=method,
                 extracted_confidence=conf, normalized_ok=ok)


def _rec(rid, *claims):
    return SourceRecord(source=claims[0].source, record_id=rid, claims=list(claims))


# --- confidence math (the "explainable confidence" selling point) ----------- #
def test_confidence_score_field_math():
    c = _c("x", "v")  # recruiter_csv(0.90) x csv_direct(1.0) x ok(1.0) x conf(1.0)
    assert score_field(c, 1, False) == 0.90              # base only
    assert score_field(c, 2, False) == 0.94              # +1 corroboration step
    assert score_field(c, 10, False) == 1.0              # corroboration capped, then clamped
    assert score_field(c, 1, True) == 0.85               # conflict penalty
    assert score_field(_c("x", "v", ok=False), 1, False) == 0.45  # normalize factor 0.5
    assert score_overall({}) == 0.0
    assert score_overall({"a": 0.8, "b": 0.6}) == 0.7
    assert source_trust("unknown") == 0.5
    assert method_factor("unknown") == 0.7
    assert priority("recruiter_csv") > priority("github")


# --- the POSITIVE fuzzy-merge branch (only non-merges were tested before) --- #
def test_fuzzy_pass_merges_when_corroborated():
    a = _rec("gh",
             _c("full_name", "Jane Q. Doe", source="github", method="github_api"),
             _c("links.github", "https://github.com/janedoe", source="github", method="github_api"),
             _c("skills", "Python", source="github", method="github_api"),
             _c("links.portfolio", "https://janedoe.dev", source="github", method="github_api"))
    b = _rec("notes",
             _c("full_name", "Jane Doe", source="recruiter_notes", method="regex"),
             _c("skills", "Python", source="recruiter_notes", method="regex"),
             _c("links.portfolio", "https://janedoe.dev/blog", source="recruiter_notes", method="regex"))
    assert len(resolve(normalize_records([a, b]))) == 1  # no strong key shared, but corroborated


def test_fuzzy_pass_blocked_by_conflicting_strong_id():
    a = _rec("gh",
             _c("full_name", "Jane Q. Doe", source="github", method="github_api"),
             _c("links.github", "https://github.com/janedoe", source="github", method="github_api"),
             _c("skills", "Python", source="github", method="github_api"))
    b = _rec("notes",
             _c("full_name", "Jane Doe", source="recruiter_notes", method="regex"),
             _c("links.github", "https://github.com/someoneelse", source="recruiter_notes", method="regex"),
             _c("skills", "Python", source="recruiter_notes", method="regex"))
    assert len(resolve(normalize_records([a, b]))) == 2  # conflicting github URLs => different people


# --- honest-null at the normalize-claim layer (the central principle) ------- #
def test_normalize_claim_honest_null_location():
    c = normalize_claim(_c("location", {"city": "SF", "region": "CA", "country": "Atlantis"}))
    assert c.value["country"] is None      # unknown country abstains
    assert c.value["city"] == "SF"         # real data preserved
    assert c.normalized_ok is False


def test_normalize_claim_honest_null_experience():
    c = normalize_claim(_c("experience",
                           {"company": "A", "title": "x", "start": "Summer 2019", "end": "garbled!!"}))
    assert c.value["start"] == "2019"      # season -> year, no fabricated month
    assert c.value["end"] is None          # unparseable -> abstain


def test_normalize_claim_years_abstains():
    c = normalize_claim(_c("years_experience", "ten"))
    assert c.value is None
    assert c.method == "normalize_failed"


# --- validation contract --------------------------------------------------- #
def test_validate_type_mismatch_raises():
    cfg = OutputConfig(fields=[FieldSpec(path="full_name", type="string")])
    with pytest.raises(SchemaValidationError):
        validate_output({"full_name": 123}, cfg)


def test_type_ok_edges():
    assert _type_ok(["a", 2], "string[]") is False     # non-string element
    assert _type_ok(True, "integer") is False          # bool is not an integer here
    assert _type_ok(True, "number") is False
    assert _type_ok(None, "string") is True            # null passes (honest-null)
    assert _type_ok(3, "number") is True


# --- skills fallback casing (short names must not be force-uppercased) ------ #
def test_skills_fallback_casing():
    assert to_canonical("rust") == ("Rust", True)
    assert to_canonical("vue") == ("Vue", True)         # NOT "VUE"
    assert to_canonical("ml") == ("ML", True)           # known acronym
    assert to_canonical("AWS") == ("AWS", True)
    assert to_canonical("GraphQL") == ("GraphQL", True)  # mixed case preserved
    assert to_canonical("   ") == (None, True)           # empty-after-clean abstains


# --- detection routing matrix ---------------------------------------------- #
def test_detect_kind_matrix(tmp_path):
    (tmp_path / "a.csv").write_text("name\nX\n", encoding="utf-8")
    (tmp_path / "n.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "u.json").write_text(json.dumps({"login": "x"}), encoding="utf-8")
    (tmp_path / "app.json").write_text(json.dumps({"name": "X"}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{{{ not json", encoding="utf-8")
    ghdir = tmp_path / "github"
    ghdir.mkdir()
    (ghdir / "z.json").write_text(json.dumps({"name": "Y"}), encoding="utf-8")

    assert detect_kind(tmp_path / "a.csv") == "recruiter_csv"
    assert detect_kind(tmp_path / "n.txt") == "recruiter_notes"
    assert detect_kind(tmp_path / "u.json") == "github"       # by content (login key)
    assert detect_kind(tmp_path / "app.json") == "ats_json"
    assert detect_kind(tmp_path / "bad.json") == "ats_json"   # unparseable -> deferred to ATS
    assert detect_kind(ghdir / "z.json") == "github"          # by path
    assert detect_kind(tmp_path / "x.pdf") is None
    assert isinstance(build_source("recruiter_csv"), RecruiterCSVSource)
    assert build_source("nope") is None


# --- projection path-DSL edges --------------------------------------------- #
def test_resolve_path_edges():
    data = {"emails": ["a@x.com"], "full_name": "Jane", "skills": [{"name": "Go"}]}
    assert resolve_path(data, "emails[99]") is None        # index out of range
    assert resolve_path(data, "full_name[].x") is None     # map over a non-list
    assert resolve_path(data, "skills[].name") == ["Go"]   # array-map happy path
    with pytest.raises(ValueError):
        resolve_path(data, "1bad")                         # invalid segment


def test_projection_sets_nested_output_path():
    prof = CanonicalProfile(candidate_id="x", full_name="Jane", emails=["jane@x.com"])
    cfg = OutputConfig(fields=[FieldSpec(path="contact.email", **{"from": "emails[0]"})])
    out = project(prof, cfg)
    assert out["contact"]["email"] == "jane@x.com"


# --- LLM enrichment wiring (regression: must wrap Claims as a SourceRecord) - #
def test_llm_enrich_wraps_claims_as_records(tmp_path, monkeypatch):
    from eightfold.llm import extractor
    monkeypatch.setattr(
        extractor, "enrich_from_text",
        lambda text, source_hint="notes": [
            _c("skills", "Python", source="recruiter_notes", method="llm_extract")])
    f = tmp_path / "notes.txt"
    f.write_text("strong python engineer", encoding="utf-8")
    out = _llm_enrich([str(f)], [_rec("csv", _c("full_name", "Jane Doe"))])
    assert all(isinstance(r, SourceRecord) for r in out)   # not a bare Claim (the bug)
    normalize_records(out)                                 # must not raise


def test_llm_cache_hit_is_offline_and_deterministic(tmp_path, monkeypatch):
    from eightfold.llm import extractor
    monkeypatch.setattr(extractor, "_CACHE_DIR", tmp_path)

    def _boom(text):
        raise AssertionError("model must not be called on a cache hit")

    monkeypatch.setattr(extractor, "_call_model", _boom)
    text = "Senior engineer fluent in Python and Go."
    (tmp_path / f"{extractor._cache_key(text)}.json").write_text(
        json.dumps({"skills": ["Python", "Go"], "summary": "Senior engineer"}), encoding="utf-8")

    claims = extractor.enrich_from_text(text)
    fields = {c.field for c in claims}
    assert "skills" in fields and "headline" in fields
    assert all(c.method == "llm_extract" for c in claims)
    assert extractor.enrich_from_text("") == []            # empty text -> no claims


# --- list-mode normalizer abstention (used by the projection layer) --------- #
def test_apply_normalizer_list_partial_failure():
    out, ok = apply_normalizer("E164", ["+1 (415) 555-0100", "call me"])
    assert out == ["+14155550100"]   # unnormalizable element dropped
    assert ok is False               # ...and reported


# --- determinism is independent of input ordering --------------------------- #
def test_determinism_across_input_permutations():
    a = json.dumps(run(INPUTS)["candidates"], sort_keys=True)
    b = json.dumps(run(list(reversed(INPUTS)))["candidates"], sort_keys=True)
    assert a == b
