"""Pipeline orchestration: files -> canonical profiles -> projected output.

    detect -> extract -> (optional LLM enrich) -> normalize -> resolve -> fuse
            -> project -> validate

The canonical build is config-agnostic and computed ONCE; the config only drives the
pure projection+validation at the end (clean write/read separation).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .detect import build_source, detect_kind
from .models import CanonicalProfile, OutputConfig, SourceRecord
from .normalize import normalize_records


@dataclass
class SourceReport:
    path: str
    source: str | None
    status: str
    records: int
    claims: int
    error: str | None = None


def build_canonical(inputs: Sequence[str | Path], *, use_llm: bool = False
                    ) -> tuple[list[CanonicalProfile], list[SourceReport]]:
    """Run the write side: produce canonical profiles + a per-source run report."""
    from .fuse import fuse
    from .resolve import resolve  # local import keeps module graph flat

    records: list[SourceRecord] = []
    reports: list[SourceReport] = []
    for raw in inputs:
        path = str(raw)
        kind = detect_kind(path)
        if not kind:
            reports.append(SourceReport(path, None, "skipped", 0, 0, "unrecognized file type"))
            continue
        src = build_source(kind)
        if src is None:  # detect_kind returned a kind with no extractor (shouldn't happen)
            reports.append(SourceReport(path, kind, "skipped", 0, 0, "no extractor"))
            continue
        result = src.extract(path)
        reports.append(SourceReport(path, kind, result.status,
                                    len(result.records),
                                    sum(len(r.claims) for r in result.records),
                                    result.error))
        records.extend(result.records)

    if use_llm:
        records = _llm_enrich(inputs, records)

    records = normalize_records(records)
    clusters = resolve(records)
    profiles = [fuse(cluster, i) for i, cluster in enumerate(clusters)]
    _ensure_unique_ids(profiles)
    # Stable output order, independent of input ordering.
    profiles.sort(key=lambda p: p.candidate_id)
    return profiles, reports


def _ensure_unique_ids(profiles: list[CanonicalProfile]) -> None:
    """Disambiguate colliding candidate_ids deterministically (two distinct people who
    legitimately don't merge but share a name). A unique base keeps its clean slug;
    collisions get a short hash of the profile's strongest identity appended."""
    counts: dict[str, int] = defaultdict(int)
    for p in profiles:
        counts[p.candidate_id] += 1
    for p in profiles:
        if counts[p.candidate_id] > 1:
            ident = "|".join(sorted(p.emails) + sorted(p.phones)
                             + [v for v in (p.links.github, p.links.linkedin) if v])
            if not ident:
                # No strong identifier: fall back to a STABLE content fingerprint rather
                # than the positional index (which depends on input ordering and would
                # make the output non-deterministic across input permutations).
                ident = json.dumps(
                    {"name": p.full_name, "location": p.location.model_dump(),
                     "skills": sorted(s.name for s in p.skills), "headline": p.headline},
                    sort_keys=True, ensure_ascii=False)
            suffix = hashlib.sha1(ident.encode()).hexdigest()[:6]
            p.candidate_id = f"{p.candidate_id}-{suffix}"


def _llm_enrich(inputs: Sequence[str | Path], records: list) -> list:
    """Optional: append low-confidence LLM claims from free-text sources."""
    try:
        from .llm.extractor import enrich_from_text
    except Exception:  # noqa: BLE001 - LLM extras absent: silently skip enrichment
        return records
    for path in inputs:
        p = Path(str(path))
        if p.suffix.lower() == ".txt" and p.exists():
            records.extend(enrich_from_text(p.read_text(encoding="utf-8", errors="replace"),
                                            source_hint=p.stem))
    return records


def run(inputs: Sequence[str | Path], config: OutputConfig | None = None, *, use_llm: bool = False
        ) -> dict[str, Any]:
    """Full pipeline -> a JSON-serializable result for one or many candidates."""
    from .project import project
    from .validate import validate_output

    config = config or OutputConfig()
    profiles, reports = build_canonical(inputs, use_llm=use_llm)
    outputs, errors = [], []
    for prof in profiles:
        # Per-candidate fault isolation: a profile that cannot satisfy the requested
        # schema is reported, never crashing the rest of the batch.
        try:
            projected = project(prof, config)
            validate_output(projected, config)
            outputs.append(projected)
        except Exception as exc:  # noqa: BLE001
            errors.append({"candidate_id": prof.candidate_id, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "candidates": outputs,
        "errors": errors,
        "report": [asdict(r) for r in reports],
    }
