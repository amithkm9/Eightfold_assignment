"""Projection engine: the path DSL, normalize, and on_missing policy."""

import pytest

from eightfold.models import CanonicalProfile, FieldSpec, Links, Location, OutputConfig, Skill
from eightfold.project import MissingFieldError, project, resolve_path


def _profile():
    return CanonicalProfile(
        candidate_id="jane-doe", full_name="Jane Doe",
        emails=["jane.doe@example.com", "jdoe@acme.com"], phones=["+14155550100"],
        location=Location(city="San Francisco", region="CA", country="US"),
        links=Links(github="https://github.com/janedoe"),
        skills=[Skill(name="Python", confidence=0.9, sources=["github"]),
                Skill(name="Go", confidence=0.8, sources=["ats_json"])],
        overall_confidence=0.72,
    )


def test_path_dsl_index_and_array_map():
    data = _profile().model_dump()
    assert resolve_path(data, "emails[0]") == "jane.doe@example.com"
    assert resolve_path(data, "location.country") == "US"
    assert resolve_path(data, "skills[].name") == ["Python", "Go"]


def test_custom_projection_remaps_and_normalizes():
    cfg = OutputConfig(fields=[
        FieldSpec(path="full_name", type="string", required=True),
        FieldSpec(path="primary_email", **{"from": "emails[0]"}, type="string"),
        FieldSpec(path="phone", **{"from": "phones[0]"}, type="string", normalize="E164"),
        FieldSpec(path="skills", **{"from": "skills[].name"}, type="string[]", normalize="canonical"),
    ])
    out = project(_profile(), cfg)
    assert out["primary_email"] == "jane.doe@example.com"
    assert out["phone"] == "+14155550100"
    assert out["skills"] == ["Python", "Go"]


def test_default_schema_toggles_provenance_and_confidence():
    out = project(_profile(), OutputConfig(include_provenance=False, include_confidence=False))
    assert "provenance" not in out
    assert "overall_confidence" not in out
    assert all("confidence" not in s for s in out["skills"])


def test_on_missing_omit_and_null():
    p = _profile()
    p.headline = None
    omit = project(p, OutputConfig(fields=[FieldSpec(path="headline")], on_missing="omit"))
    assert "headline" not in omit
    nul = project(p, OutputConfig(fields=[FieldSpec(path="headline")], on_missing="null"))
    assert nul["headline"] is None


def test_on_missing_error_raises():
    p = _profile()
    p.headline = None
    with pytest.raises(MissingFieldError):
        project(p, OutputConfig(fields=[FieldSpec(path="headline", required=True)], on_missing="error"))
