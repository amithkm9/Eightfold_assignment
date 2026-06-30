"""Projection layer — the "required twist".

A PURE, deterministic function `(CanonicalProfile, OutputConfig) -> dict`. The config
is DATA: it never changes the fusion engine, only how the already-built canonical
record is shaped on the way out. Supports:

  - select a subset of fields
  - remap via a `from` path expression: dotted (location.country), indexed (emails[0]),
    array-map (skills[].name)
  - per-field normalize (reusing the SAME normalizer registry as stage 3)
  - toggle provenance / confidence
  - on_missing policy: null | omit | error
"""

from __future__ import annotations

import re
from typing import Any

from .models import CanonicalProfile, OutputConfig
from .normalize import apply_normalizer


class MissingFieldError(ValueError):
    """Raised when a value is missing and on_missing == 'error'."""


# --------------------------------------------------------------------------- #
# Path expression resolver
# --------------------------------------------------------------------------- #
def _tokenize(path: str):
    toks: list[tuple] = []
    for part in path.split("."):
        m = re.match(r"^([A-Za-z_]\w*)((?:\[\d*\])*)$", part)
        if not m:
            raise ValueError(f"invalid path segment: {part!r} in {path!r}")
        toks.append(("key", m.group(1)))
        for b in re.findall(r"\[(\d*)\]", m.group(2)):
            toks.append(("map",) if b == "" else ("index", int(b)))
    return toks


def _resolve(cur: Any, toks: list[tuple]) -> Any:
    if not toks or cur is None:
        return cur
    kind, *arg = toks[0]
    rest = toks[1:]
    if kind == "key":
        nxt = cur.get(arg[0]) if isinstance(cur, dict) else None
        return _resolve(nxt, rest)
    if kind == "index":
        i = arg[0]
        nxt = cur[i] if isinstance(cur, list) and len(cur) > i else None
        return _resolve(nxt, rest)
    if kind == "map":
        if not isinstance(cur, list):
            return None
        mapped = [_resolve(item, rest) for item in cur]
        return [v for v in mapped if v is not None]
    return None


def resolve_path(obj: dict, path: str) -> Any:
    return _resolve(obj, _tokenize(path))


def _is_missing(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _base_field(path: str) -> str:
    """The top-level canonical field a path reads from (for provenance lookup)."""
    return _tokenize(path)[0][1]


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #
def project(profile: CanonicalProfile, config: OutputConfig) -> dict:
    data = profile.model_dump()  # canonical dict (field_confidence is excluded)

    # ---- Default schema: emit canonical, honoring the toggles ----
    if not config.fields:
        default_out = dict(data)
        if not config.include_provenance:
            default_out.pop("provenance", None)
        if not config.include_confidence:
            default_out.pop("overall_confidence", None)
            for sk in default_out.get("skills", []):
                sk.pop("confidence", None)
        return default_out

    # ---- Custom projection ----
    out: dict[str, Any] = {}
    used_base_fields: set[str] = set()
    for spec in config.fields:
        src_path = spec.from_ or spec.path
        value = resolve_path(data, src_path)
        if spec.normalize and not _is_missing(value):
            value, _ok = apply_normalizer(spec.normalize, value)

        if _is_missing(value):
            if config.on_missing == "omit":
                continue
            if config.on_missing == "error":
                raise MissingFieldError(f"missing value for '{spec.path}' (from '{src_path}')")
            value = None  # on_missing == "null"

        _set_path(out, spec.path, value)
        used_base_fields.add(_base_field(src_path))

    if config.include_confidence:
        out["overall_confidence"] = profile.overall_confidence
    if config.include_provenance:
        out["provenance"] = [p.model_dump() for p in profile.provenance
                             if p.field in used_base_fields]
    return out


def _set_path(out: dict, path: str, value: Any) -> None:
    """Support nested output paths (dots); arrays not needed on the output side."""
    parts = path.split(".")
    cur = out
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
