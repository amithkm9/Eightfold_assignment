"""Explainable confidence model.

Every number traces back to inputs, so "why 0.82?" is answerable in the demo:

    field_confidence = clamp(
        base_source_trust x method_factor x normalize_factor
        + corroboration_bonus            # independent sources that agree
        - conflict_penalty,              # sources that actively disagreed
        0, 1)

There is no magic constant that isn't defined here.
"""

from __future__ import annotations

# Prior trust in a source, independent of field. Structured > parsed prose.
SOURCE_TRUST = {
    "recruiter_csv": 0.90,
    "ats_json": 0.85,
    "linkedin": 0.78,
    "github": 0.80,
    "resume": 0.62,
    "recruiter_notes": 0.55,
}

# How the value was obtained. Direct reads beat inference; LLM is least trusted.
METHOD_FACTOR = {
    "csv_direct": 1.00,
    "json_remap": 0.97,
    "github_api": 0.97,
    "regex": 0.85,
    "llm_extract": 0.65,
    "normalize_failed": 0.0,
}

# Priority order for picking a single winner (higher = preferred).
WINNER_PRIORITY = ["recruiter_csv", "ats_json", "linkedin", "github", "resume", "recruiter_notes"]

CORROBORATION_STEP = 0.04
CORROBORATION_CAP = 0.10
CONFLICT_PENALTY = 0.05


def source_trust(source: str) -> float:
    return SOURCE_TRUST.get(source, 0.5)


def method_factor(method: str) -> float:
    return METHOD_FACTOR.get(method, 0.7)


def priority(source: str) -> int:
    try:
        return len(WINNER_PRIORITY) - WINNER_PRIORITY.index(source)
    except ValueError:
        return 0


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, round(x, 4)))


def score_field(winner, agreeing_sources: int, conflicted: bool) -> float:
    """Confidence for a single resolved field.

    `winner` is the winning Claim. `agreeing_sources` counts DISTINCT sources whose
    value matched the winner (>=1). `conflicted` is True if some source disagreed.
    """
    base = (source_trust(winner.source)
            * method_factor(winner.method)
            * (1.0 if winner.normalized_ok else 0.5)
            * float(winner.extracted_confidence))
    corroboration = min(CORROBORATION_CAP, CORROBORATION_STEP * max(0, agreeing_sources - 1))
    penalty = CONFLICT_PENALTY if conflicted else 0.0
    return _clamp(base + corroboration - penalty)


def score_overall(field_confidences: dict[str, float]) -> float:
    """Mean of the resolved per-field confidences (each already incorporates source
    trust via score_field). Empty profile -> 0.

    NOTE: this is a plain unweighted mean — it treats every field as equally important
    and is not monotonic in coverage (adding a real but low-confidence field can lower
    the overall number). A production version would importance-weight fields and
    calibrate the score against labeled outcomes; see README "Scaling to production".
    """
    if not field_confidences:
        return 0.0
    return _clamp(sum(field_confidences.values()) / len(field_confidences))
