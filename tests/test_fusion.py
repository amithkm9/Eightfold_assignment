"""Fusion / survivorship: conflict resolution, dedup, union, provenance."""

from eightfold.fuse import fuse
from eightfold.models import Claim, SourceRecord
from eightfold.normalize import normalize_records
from eightfold.resolve import resolve


def _c(field, value, source, method, conf=0.8):
    return Claim(field=field, value=value, source=source, method=method, extracted_confidence=conf)


def _records():
    csv = SourceRecord(source="recruiter_csv", record_id="csv:0", claims=[
        _c("full_name", "Jane Doe", "recruiter_csv", "csv_direct", 0.92),
        _c("emails", "jane@example.com", "recruiter_csv", "csv_direct", 0.92),
        _c("phones", "+1 (415) 555-0100", "recruiter_csv", "csv_direct", 0.92),
    ])
    github = SourceRecord(source="github", record_id="github:janedoe", claims=[
        _c("full_name", "Jane Q. Doe", "github", "github_api", 0.8),
        _c("skills", "Python", "github", "github_api", 0.95),
        _c("links.portfolio", "https://janedoe.dev", "github", "github_api", 0.9),
        _c("location", {"city": "SF", "region": None, "country": "USA"}, "github", "github_api", 0.6),
    ])
    notes = SourceRecord(source="recruiter_notes", record_id="notes:0", claims=[
        _c("emails", "jane@example.com", "recruiter_notes", "regex", 0.7),
        _c("phones", "415.555.0100", "recruiter_notes", "regex", 0.7),  # different format, same number
        _c("skills", "Python", "recruiter_notes", "regex", 0.55),
        _c("links.portfolio", "https://janedoe.dev/blog", "recruiter_notes", "regex", 0.5),
    ])
    return [csv, github, notes]


def _fused():
    clusters = resolve(normalize_records(_records()))
    assert len(clusters) == 1, "all three records should resolve to one entity"
    return fuse(clusters[0])


def test_name_winner_is_highest_trust_source():
    # CSV (trust .9) beats GitHub display name (trust .8).
    assert _fused().full_name == "Jane Doe"


def test_phone_dedup_across_formats():
    # Two different formats of the same number -> one E.164 value.
    assert _fused().phones == ["+14155550100"]


def test_skills_union_records_corroborating_sources():
    py = next(s for s in _fused().skills if s.name == "Python")
    assert set(py.sources) == {"github", "recruiter_notes"}


def test_provenance_is_populated_and_traceable():
    prov = _fused().provenance
    assert any(p.field == "full_name" and p.source == "recruiter_csv" for p in prov)
    # No exact-duplicate provenance entries.
    keys = [(p.field, p.source, p.method) for p in prov]
    assert len(keys) == len(set(keys))


def test_garbage_source_does_not_break_fusion():
    recs = _records() + [SourceRecord(source="ats_json", record_id="ats:x", claims=[])]
    clusters = resolve(normalize_records(recs))
    assert fuse(clusters[0]).full_name == "Jane Doe"
