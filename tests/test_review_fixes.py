"""Regression tests for the issues found in the architecture/correctness review."""

import glob
import json
import os
from pathlib import Path

from eightfold.detect import detect_kind
from eightfold.fuse import _years_from_experience, fuse
from eightfold.models import Claim, Experience, OutputConfig, SourceRecord
from eightfold.normalize import normalize_records
from eightfold.normalize.dates import to_year_month
from eightfold.pipeline import _ensure_unique_ids, run
from eightfold.resolve import resolve

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
