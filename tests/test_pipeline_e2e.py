"""End-to-end pipeline on the sample inputs, plus gold-profile comparison."""

import glob
import json
import os
from pathlib import Path

from eightfold.models import OutputConfig
from eightfold.pipeline import run

ROOT = Path(__file__).resolve().parent.parent
INPUTS = [f for f in glob.glob(str(ROOT / "samples/inputs/**/*"), recursive=True) if os.path.isfile(f)]
EXPECTED = ROOT / "samples/expected"


def _jane(candidates, key="candidate_id", val="jane-doe"):
    return next(c for c in candidates if c.get(key) == val)


def test_default_run_produces_expected_candidates():
    res = run(INPUTS)
    ids = sorted(c["candidate_id"] for c in res["candidates"])
    assert ids == ["broken-row-no-name", "jane-doe", "john-smith", "maria-garcia"]


def test_jane_matches_gold_default():
    res = run(INPUTS)
    jane = _jane(res["candidates"])
    gold = json.loads((EXPECTED / "jane_default.json").read_text())
    assert jane == gold


def test_jane_matches_gold_custom():
    cfg = OutputConfig.model_validate(json.loads((ROOT / "config/example_custom.json").read_text()))
    res = run(INPUTS, cfg)
    jane = _jane(res["candidates"], key="full_name", val="Jane Doe")
    gold = json.loads((EXPECTED / "jane_custom.json").read_text())
    assert jane == gold


def test_determinism_byte_identical_across_runs():
    a = json.dumps(run(INPUTS)["candidates"], sort_keys=True)
    b = json.dumps(run(INPUTS)["candidates"], sort_keys=True)
    assert a == b


def test_jane_merged_all_four_sources():
    jane = _jane(run(INPUTS)["candidates"])
    assert jane["full_name"] == "Jane Doe"            # csv won over github "Jane Q. Doe"
    assert set(jane["emails"]) == {"jane.doe@example.com", "jdoe@acme.com"}  # union
    assert jane["phones"] == ["+14155550100"]          # 3 formats deduped
    assert jane["location"] == {"city": "San Francisco", "region": "CA", "country": "US"}
    assert jane["links"]["github"] == "https://github.com/janedoe"  # github authoritative
