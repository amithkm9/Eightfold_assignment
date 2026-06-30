"""Normalization registry + the stage-3 claim normalizer.

The SAME registry is used in two places — when building the canonical record
(`normalize_claim`) and when the projection layer applies a per-field `normalize`
directive — so a value is normalized identically no matter which path produced it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import Claim, Method, SourceRecord
from .country import to_iso3166
from .dates import to_year, to_year_month
from .phone import to_e164
from .skills import to_canonical

# name -> fn(value) -> (normalized_value, ok)
NORMALIZERS: dict[str, Callable[[Any], tuple[Any, bool]]] = {
    "E164": to_e164,
    "YYYY-MM": to_year_month,
    "ISO3166": to_iso3166,
    "canonical": to_canonical,
    "year": to_year,
}


def apply_normalizer(name: str | None, value: Any) -> tuple[Any, bool]:
    """Used by the projection layer. Unknown normalizer name -> passthrough."""
    if name is None:
        return value, True
    fn = NORMALIZERS.get(name)
    if fn is None:
        return value, True
    if isinstance(value, list):
        out, ok_all = [], True
        for v in value:
            nv, ok = fn(v)
            ok_all = ok_all and ok
            if nv is not None:
                out.append(nv)
        return out, ok_all
    return fn(value)


def _fail(claim: Claim) -> Claim:
    """A value that could not be normalized abstains to None (honest-null)."""
    return claim.with_value(None, normalizer=claim.normalizer, ok=False,
                            method=Method.NORMALIZE_FAILED.value)


def normalize_claim(claim: Claim) -> Claim:
    """Stage 3: normalize one claim's value according to its canonical field."""
    field = claim.field

    if field == "phones":
        v, ok = to_e164(claim.value)
        return claim.with_value(v, normalizer="E164", ok=ok) if ok else _fail(claim)

    if field == "skills":
        v, ok = to_canonical(claim.value)
        return claim.with_value(v, normalizer="canonical", ok=ok) if ok else _fail(claim)

    if field == "location" and isinstance(claim.value, dict):
        loc = dict(claim.value)
        country, ok = to_iso3166(loc.get("country"))
        loc["country"] = country
        return claim.with_value(loc, normalizer="ISO3166", ok=ok)

    if field == "experience" and isinstance(claim.value, dict):
        exp = dict(claim.value)
        start, sok = to_year_month(exp.get("start"))
        end, eok = to_year_month(exp.get("end"))
        exp["start"], exp["end"] = start, end
        return claim.with_value(exp, normalizer="YYYY-MM", ok=sok and eok)

    if field == "education" and isinstance(claim.value, dict):
        edu = dict(claim.value)
        yr, ok = to_year(edu.get("end_year"))
        edu["end_year"] = yr
        return claim.with_value(edu, normalizer="year", ok=ok)

    if field == "years_experience":
        try:
            return claim.with_value(float(claim.value), normalizer=None, ok=True)
        except (TypeError, ValueError):
            return _fail(claim)

    # full_name, emails, headline, links.* -> light cleanup, no transform
    if isinstance(claim.value, str):
        return claim.with_value(" ".join(claim.value.split()), normalizer=None, ok=True)
    return claim


def normalize_records(records: list[SourceRecord]) -> list[SourceRecord]:
    """Normalize every claim across a list of SourceRecords (returns new records)."""
    out = []
    for rec in records:
        rec = rec.model_copy(update={"claims": [normalize_claim(c) for c in rec.claims]})
        out.append(rec)
    return out
