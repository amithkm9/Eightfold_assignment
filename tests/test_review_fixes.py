"""Regression tests for the issues found in the architecture/correctness review."""

import copy
import glob
import json
import os
from pathlib import Path

from eightfold.cli import _expand_inputs
from eightfold.detect import detect_kind
from eightfold.fuse import _years_from_experience, fuse
from eightfold.models import Claim, Experience, OutputConfig, SourceRecord
from eightfold.normalize import normalize_records
from eightfold.normalize.dates import to_year_month
from eightfold.pipeline import _ensure_unique_ids, run
from eightfold.resolve import resolve
from eightfold.sources.github_client import GitHubSource
from eightfold.sources.recruiter_notes import RecruiterNotesSource

ROOT = Path(__file__).resolve().parent.parent
INPUTS = [f for f in glob.glob(str(ROOT / "samples/inputs/**/*"), recursive=True) if os.path.isfile(f)]


def _rec(rid, *claims):
    return SourceRecord(source=claims[0].source, record_id=rid, claims=list(claims))


def _c(field, value, source="recruiter_csv", method="csv_direct", conf=0.9):
    return Claim(field=field, value=value, source=source, method=method, extracted_confidence=conf)


# --- include_confidence=False must NOT drop candidates that have skills ----- #
def test_include_confidence_false_keeps_skilled_candidates():
    res = run(INPUTS, OutputConfig(include_confidence=False))
    jane = next((c for c in res["candidates"] if c.get("candidate_id") == "jane-doe"), None)
    assert jane is not None, "candidate with skills disappeared when confidence was toggled off"
    assert jane["skills"] and all("confidence" not in s for s in jane["skills"])
    assert not any(e["candidate_id"] == "jane-doe" for e in res["errors"])


# --- fuzzy pass must not merge people with CONFLICTING strong identifiers --- #
def test_conflicting_emails_block_fuzzy_merge():
    a = _rec("a", _c("full_name", "John Smith"), _c("emails", "john.a@aaa.com"),
             _c("skills", "Python"), _c("location", {"city": "New York", "region": None, "country": None}))
    b = _rec("b", _c("full_name", "John Smith"), _c("emails", "john.b@bbb.com"),
             _c("skills", "Python"), _c("location", {"city": "New York", "region": None, "country": None}))
    clusters = resolve(normalize_records([a, b]))
    assert len(clusters) == 2, "distinct people with different emails were wrongly merged"


# --- name-only records sharing a common name must NOT hard-merge ------------ #
def test_name_only_common_name_does_not_merge():
    a = _rec("a", _c("full_name", "Maria Garcia"))
    b = _rec("b", _c("full_name", "Maria Garcia"))
    clusters = resolve(normalize_records([a, b]))
    assert len(clusters) == 2


# --- dates must not fabricate a month from a season/approximate year -------- #
def test_dates_year_only_words_keep_year():
    assert to_year_month("Summer 2019") == ("2019", True)
    assert to_year_month("circa 2015") == ("2015", True)
    assert to_year_month("March 2021") == ("2021-03", True)  # real month still works


# --- ATS blob with a `languages` key must not be misread as GitHub --------- #
def test_ats_with_languages_not_detected_as_github(tmp_path):
    f = tmp_path / "ats_export.json"
    f.write_text(json.dumps({"full_name": "X", "languages": ["English", "Hindi"],
                             "tech_stack": ["Python"]}), encoding="utf-8")
    assert detect_kind(f) == "ats_json"


# --- years_experience must merge overlapping spans (no double counting) ----- #
def test_years_experience_merges_overlapping_spans():
    concurrent = [Experience(company="A", title="x", start="2010-01", end="2012-01"),
                  Experience(company="B", title="y", start="2010-01", end="2012-01")]
    assert _years_from_experience(concurrent) == 2.0  # not 4.0
    sequential = [Experience(company="A", title="x", start="2010-01", end="2012-01"),
                  Experience(company="B", title="y", start="2012-01", end="2014-01")]
    assert _years_from_experience(sequential) == 4.0


# --- candidate_id collisions get disambiguated deterministically ----------- #
def test_candidate_ids_are_unique():
    a = fuse([_rec("a", _c("full_name", "John Smith"), _c("emails", "john.a@aaa.com"))])
    b = fuse([_rec("b", _c("full_name", "John Smith"), _c("emails", "john.b@bbb.com"))])
    assert a.candidate_id == b.candidate_id == "john-smith"
    profiles = [a, b]
    _ensure_unique_ids(profiles)
    assert profiles[0].candidate_id != profiles[1].candidate_id
    assert all(p.candidate_id.startswith("john-smith") for p in profiles)


# --- non-dict experience claim must not crash fusion ----------------------- #
def test_non_dict_experience_claim_is_tolerated():
    rec = _rec("a", _c("full_name", "Jane Doe"), _c("experience", "garbled string"))
    prof = fuse([rec])
    assert prof.full_name == "Jane Doe"  # did not raise


# ========================================================================== #
#  Second review round — bugs #1-#4 + safe robustness fixes                   #
# ========================================================================== #


# --- #1 GitHub free-text location: a US state must not become a country ---- #
def test_github_us_state_not_mislabeled_as_country():
    src = GitHubSource()
    recs = src._records_from_profile({"login": "x", "name": "X", "location": "San Francisco, CA"})
    loc = next(c.value for c in recs[0].claims if c.field == "location")
    assert loc["city"] == "San Francisco"
    assert loc["region"] == "CA"
    assert loc["country"] is None  # "CA" (=Canada) must NOT be inferred from a US state
    # a genuine country still maps through
    recs2 = src._records_from_profile({"login": "y", "name": "Y", "location": "Bengaluru, India"})
    loc2 = next(c.value for c in recs2[0].claims if c.field == "location")
    assert loc2["country"] == "India"


# --- #2 same name + same company must not force a hard merge --------------- #
def test_same_name_company_with_conflicting_ids_not_merged():
    a = _rec("a", _c("full_name", "Michael Chen"), _c("emails", "m.chen@aaa.com"),
             _c("experience", {"company": "Amazon", "title": "SWE"}))
    b = _rec("b", _c("full_name", "Michael Chen"), _c("emails", "m.chen@bbb.com"),
             _c("experience", {"company": "Amazon", "title": "SDM"}))
    clusters = resolve(normalize_records([a, b]))
    assert len(clusters) == 2, "different people sharing name+company were force-merged"


def test_same_name_company_alone_does_not_merge():
    a = _rec("a", _c("full_name", "Michael Chen"), _c("experience", {"company": "Amazon", "title": "SWE"}))
    b = _rec("b", _c("full_name", "Michael Chen"), _c("experience", {"company": "Amazon", "title": "SWE"}))
    clusters = resolve(normalize_records([a, b]))
    assert len(clusters) == 2  # name+company alone is insufficient evidence to merge


# --- #3 notes name label is case-insensitive AND word-bounded -------------- #
def test_notes_name_label_case_insensitive(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("Name: Jane Doe\nGreat candidate.", encoding="utf-8")
    recs = RecruiterNotesSource()._extract(f)
    names = [c.value for r in recs for c in r.claims if c.field == "full_name"]
    assert names == ["Jane Doe"]  # capitalized "Name:" label now matches


def test_notes_name_label_ignores_substring_labels(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("Username: Bobby\nnickname: Spark\nno labelled name here.", encoding="utf-8")
    recs = RecruiterNotesSource()._extract(f)
    names = [c.value for r in recs for c in r.claims if c.field == "full_name"]
    assert names == []  # "Username:"/"nickname:" must not be read as a name label


# --- #4 dates must never fabricate a month from ranges/substrings ---------- #
def test_dates_no_fabricated_month_from_substring_or_range():
    assert to_year_month("Maryland 2018") == ("2018", True)   # "mar" substring is NOT a month
    assert to_year_month("2019-13") == ("2019", True)         # invalid month -> keep the year
    assert to_year_month("March 2021") == ("2021-03", True)   # a real month still works
    assert to_year_month("May 2019") == ("2019-05", True)     # short real month still works
    range_val, ok = to_year_month("2018-2020")                # a year range -> no "-MM"
    assert range_val in (None, "2018", "2020")


# --- safe robustness: a malformed LLM cache entry must not crash the run --- #
def test_llm_bad_cache_shape_does_not_crash(tmp_path, monkeypatch):
    from eightfold.llm import extractor
    monkeypatch.setattr(extractor, "_CACHE_DIR", tmp_path)
    text = "Senior engineer fluent in Python and Go."
    key = extractor._cache_key(text)
    (tmp_path / f"{key}.json").write_text(json.dumps({"skills": None, "summary": None}),
                                          encoding="utf-8")
    assert extractor.enrich_from_text(text) == []  # guarded, no TypeError


# --- safe robustness: input expansion de-duplicates overlapping paths ------ #
def test_expand_inputs_dedupes_overlapping_paths(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    f = d / "a.csv"
    f.write_text("name\nX\n", encoding="utf-8")
    expanded = _expand_inputs([str(d), str(f)])  # dir + an explicit file inside it
    assert len(expanded) == 1


# --- safe robustness: unique-id suffix is stable across input orderings ---- #
def test_unique_ids_are_input_order_independent():
    p1 = fuse([_rec("a", _c("full_name", "Sam Lee"), _c("skills", "Python"))])
    p2 = fuse([_rec("b", _c("full_name", "Sam Lee"), _c("skills", "Java"))])
    assert p1.candidate_id == p2.candidate_id == "sam-lee"  # collide before disambiguation
    fwd = [copy.deepcopy(p1), copy.deepcopy(p2)]
    rev = [copy.deepcopy(p2), copy.deepcopy(p1)]
    _ensure_unique_ids(fwd)
    _ensure_unique_ids(rev)
    assert fwd[0].candidate_id != fwd[1].candidate_id          # disambiguated
    assert fwd[0].candidate_id == rev[1].candidate_id          # same profile -> same id, any order
