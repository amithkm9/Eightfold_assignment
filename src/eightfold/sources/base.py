"""Source extraction contract.

Every extractor turns one input artifact into `SourceRecord`s (each a bundle of
`Claim`s about one candidate). Extraction is wrapped so that a missing, empty, or
malformed source degrades to an empty result with a `parse_status` — it NEVER raises
into the pipeline. This is the "robust / degrade gracefully" constraint, implemented
as failure-as-value rather than control-flow exceptions.
"""

from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import ParseStatus, SourceRecord


@dataclass
class ExtractResult:
    """Outcome of extracting one source. Always returned; never thrown."""

    source: str
    status: str = ParseStatus.OK.value
    records: list[SourceRecord] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == ParseStatus.OK.value


class Source(ABC):
    """Base class for all source extractors."""

    kind: str = "unknown"

    @abstractmethod
    def _extract(self, path: Path) -> list[SourceRecord]:
        """Parse `path` into source records. May raise; the wrapper catches it."""
        raise NotImplementedError

    def extract(self, path: str | Path) -> ExtractResult:
        """Safe entry point: catches everything and reports it as status, not a crash."""
        p = Path(path)
        if not p.exists():
            return ExtractResult(self.kind, ParseStatus.FAILED.value, error=f"missing file: {p}")
        try:
            records = self._extract(p)
        except Exception as exc:  # noqa: BLE001 - deliberate: isolate one bad source
            return ExtractResult(
                self.kind,
                ParseStatus.FAILED.value,
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}",
            )
        if not records or all(not r.claims for r in records):
            return ExtractResult(self.kind, ParseStatus.EMPTY.value, records=records)
        return ExtractResult(self.kind, ParseStatus.OK.value, records=records)
