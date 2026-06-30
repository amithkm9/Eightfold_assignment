"""Shared text helpers used by both entity resolution and fusion.

Kept in one place so the matching/identity logic can never silently disagree
between the resolve and fuse stages.
"""

from __future__ import annotations

import re
from typing import Any


def name_key(value: Any) -> str:
    """Normalize a name for matching: lowercase, drop dots, collapse whitespace."""
    return " ".join(str(value).lower().replace(".", "").split())


def slug(value: Any) -> str:
    """Filesystem/URL-safe slug from arbitrary text."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(value).lower())).strip("-")


def norm_url(url: Any) -> str:
    """Strip scheme / www / trailing slash so URLs compare structurally."""
    s = str(url).strip().lower().rstrip("/")
    for prefix in ("https://", "http://", "www."):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s


def url_host(url: Any) -> str:
    """Host portion of a (possibly schemeless) URL."""
    s = norm_url(url)
    return s.split("/")[0] if s else ""
