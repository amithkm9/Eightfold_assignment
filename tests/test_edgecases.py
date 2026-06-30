"""Edge cases the brief cares about: robustness, abstention, validation errors."""

import glob
import os
from pathlib import Path

from eightfold.models import FieldSpec, OutputConfig
from eightfold.pipeline import build_canonical, run

ROOT = Path(__file__).resolve().parent.parent
INPUTS = [f for f in glob.glob(str(ROOT / "samples/inputs/**/*"), recursive=True) if os.path.isfile(f)]


def test_malformed_source_does_not_crash_and_is_reported():
    _, reports = build_canonical(INPUTS)
    failed = [r for r in reports if r.status == "failed"]
    assert failed, "the malformed JSON should be reported as failed, not crash the run"
    assert any("malformed" in r.path for r in failed)


def test_empty_source_is_reported_empty():
    _, reports = build_canonical(INPUTS)
    assert any(r.status == "empty" for r in reports)


def test_required_field_violation_is_isolated_not_fatal():
    # broken-row candidate has no full_name; custom config marks it required.
    cfg = OutputConfig(fields=[
        FieldSpec(path="full_name", type="string", required=True),
        FieldSpec(path="primary_email", **{"from": "emails[0]"}, type="string", required=True),
    ])
    res = run(INPUTS, cfg)
    assert any(e["candidate_id"] == "broken-row-no-name" for e in res["errors"])
    # ...but the valid candidates still come through.
    assert any(c.get("full_name") == "Jane Doe" for c in res["candidates"])


def test_unnormalizable_phone_is_dropped_not_invented():
    # No candidate should ever carry a non-E.164 phone string.
    for c in run(INPUTS)["candidates"]:
        for ph in c.get("phones", []):
            assert ph.startswith("+") and ph[1:].isdigit()


def test_unknown_file_type_is_skipped():
    skip_input = INPUTS + [str(ROOT / "README.md")]  # may not exist yet; tolerate
    res = run([p for p in skip_input if os.path.exists(p)])
    assert res["candidates"]  # run still succeeds
