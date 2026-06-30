"""Source-type detection: map an input file to the extractor that understands it.

Detection is by extension first, with content sniffing to tell a GitHub fixture
(`login`/`html_url`) apart from an ATS blob (both are .json). Unknown files are
skipped rather than guessed at.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import SourceKind
from .sources.ats_json import ATSJsonSource
from .sources.base import Source
from .sources.github_client import GitHubSource
from .sources.recruiter_csv import RecruiterCSVSource
from .sources.recruiter_notes import RecruiterNotesSource


def detect_kind(path: str | Path) -> str | None:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        return SourceKind.RECRUITER_CSV.value
    if suffix == ".txt":
        return SourceKind.RECRUITER_NOTES.value
    if suffix == ".json":
        # Disambiguate GitHub fixture vs ATS blob by content / location. We rely on
        # GitHub-specific keys (login / html_url) only — NOT `languages`, which ATS
        # exports also use (for spoken languages) and would misclassify.
        if "github" in p.parts or "github" in p.stem.lower():
            return SourceKind.GITHUB.value
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - unparseable JSON: let the ATS extractor report it
            return SourceKind.ATS_JSON.value
        if isinstance(data, dict) and ("login" in data or "html_url" in data):
            return SourceKind.GITHUB.value
        return SourceKind.ATS_JSON.value
    return None


_SOURCES = {
    SourceKind.RECRUITER_CSV.value: RecruiterCSVSource,
    SourceKind.ATS_JSON.value: ATSJsonSource,
    SourceKind.GITHUB.value: GitHubSource,
    SourceKind.RECRUITER_NOTES.value: RecruiterNotesSource,
}


def build_source(kind: str) -> Source | None:
    cls = _SOURCES.get(kind)
    return cls() if cls else None  # type: ignore[abstract]  # values are concrete Source subclasses
