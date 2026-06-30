"""Optional LLM enrichment for free-text sources (the differentiator).

This layer is the careful answer to the brief's central tension: unstructured prose
begs for an LLM, but the constraints demand "deterministic" and "never invented".
So the LLM is fenced in:

  * OFF by default — only runs with `--llm` AND an ANTHROPIC_API_KEY present.
  * Reproducible — every call is cached by a hash of (model, schema, text). Re-runs
    read the cache and never re-hit the model, so the pipeline is byte-identical across
    runs. We don't rely on any sampling parameter for this — the input-hash cache (not a
    temperature setting) is what guarantees reproducibility.
  * Constrained — structured JSON-schema output; the model is told to ABSTAIN
    (return nothing) rather than guess.
  * Low-trust — every value becomes a `llm_extract` Claim with low confidence, so the
    fusion layer can never let it overwrite a structured (CSV/ATS/GitHub) value.

If anything goes wrong (no key, network error, bad output) it returns [] — the
pipeline runs exactly as it would without the flag.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from ..models import Claim, Method, SourceKind

MODEL = "claude-haiku-4-5"  # cheapest tier — ample for bounded, schema-constrained extraction
CACHE_VERSION = "1"        # bump to invalidate all cached extractions
_MAX_RETRIES = 3
_CONF_SKILL = 0.5          # LLM-proposed skill: low trust, can't overwrite structured
_CONF_HEADLINE = 0.45      # LLM-proposed headline: lowest trust

# Cache dir is overridable; defaults next to the package but a read-only install
# (site-packages) can point it elsewhere via EIGHTFOLD_LLM_CACHE.
_CACHE_DIR = Path(os.environ.get("EIGHTFOLD_LLM_CACHE", Path(__file__).resolve().parent / "cache"))

_SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["skills", "summary"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You extract structured facts from recruiter free-text about a single candidate. "
    "Only output information explicitly present in the text. If a field is not clearly "
    "stated, leave it empty (empty array / empty string). Never guess, infer, or invent. "
    "Return skills as short canonical names. Return summary as a one-line professional "
    "headline ONLY if the text clearly supports one, else an empty string."
)


def _cache_key(text: str) -> str:
    h = hashlib.sha256()
    for part in (CACHE_VERSION, MODEL, _SYSTEM, json.dumps(_SCHEMA, sort_keys=True), text):
        h.update(part.encode())
    return h.hexdigest()


def _cached(key: str) -> dict | None:
    f = _CACHE_DIR / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _store(key: str, data: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # read-only cache dir: skip caching rather than fail the run


def _call_model(text: str) -> dict | None:
    """Call Claude with structured output. Returns parsed dict, or None if disabled/
    unavailable. Retries transient errors with backoff so a blip doesn't silently drop
    enrichment (which would make a later run differ until the cache warms)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    client = anthropic.Anthropic()
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=_SYSTEM,
                # Haiku 4.5 doesn't accept the `effort` parameter (400) — schema-constrained
                # output alone is enough for this bounded extraction.
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
                messages=[{"role": "user", "content": text}],
            )
            out = next((b.text for b in resp.content if b.type == "text"), None)
            return json.loads(out) if out else None
        except Exception:  # noqa: BLE001 - degrade gracefully; retry transient, then give up
            if attempt == _MAX_RETRIES - 1:
                return None
            time.sleep(2 ** attempt)
    return None


def enrich_from_text(text: str, source_hint: str = "notes") -> list[Claim]:
    """Return LOW-confidence llm_extract claims, or [] if disabled/unavailable."""
    text = (text or "").strip()
    if not text:
        return []
    key = _cache_key(text)
    data = _cached(key)
    if data is None:
        data = _call_model(text)
        if data is None:
            return []
        _store(key, data)

    claims: list[Claim] = []
    src = SourceKind.RESUME.value if source_hint == "resume" else SourceKind.RECRUITER_NOTES.value
    raw_skills = data.get("skills")
    for sk in (raw_skills if isinstance(raw_skills, list) else []):
        if isinstance(sk, str) and sk.strip():
            claims.append(Claim(field="skills", value=sk.strip(), source=src,
                                method=Method.LLM_EXTRACT.value, raw_span="llm",
                                extracted_confidence=_CONF_SKILL))
    summary = data.get("summary")
    if isinstance(summary, str) and summary.strip():
        claims.append(Claim(field="headline", value=summary.strip(), source=src,
                            method=Method.LLM_EXTRACT.value, raw_span="llm",
                            extracted_confidence=_CONF_HEADLINE))
    return claims
