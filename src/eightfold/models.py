"""Core data models.

Two worlds live here:

1. The *write side* — `Claim` / `SourceRecord`: every value enters the system as a
   CLAIM that already carries where it came from (`source`), how it was obtained
   (`method`), the raw text it was derived from (`raw_span`), and how sure the
   *extractor* was (`extracted_confidence`). Because provenance + confidence travel
   with the value from birth, they are an invariant — not something bolted on later.

2. The *read side* — `CanonicalProfile`: the one clean, deduplicated, fully
   provenance-tagged record. It maps 1:1 to the brief's default output schema. The
   projection layer (see project.py) turns this into whatever a runtime config asks for.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Vocabulary — keeping source kinds and methods as constants makes provenance
# greppable and the confidence model's lookup tables explicit.
# --------------------------------------------------------------------------- #
class SourceKind(str, Enum):
    RECRUITER_CSV = "recruiter_csv"
    ATS_JSON = "ats_json"
    GITHUB = "github"
    RECRUITER_NOTES = "recruiter_notes"
    RESUME = "resume"
    LINKEDIN = "linkedin"


class Method(str, Enum):
    CSV_DIRECT = "csv_direct"        # value read straight from a structured cell
    JSON_REMAP = "json_remap"        # value pulled from a foreign JSON field name
    GITHUB_API = "github_api"        # value from the GitHub client (authoritative for its domain)
    REGEX = "regex"                  # deterministic pattern match over free text
    LLM_EXTRACT = "llm_extract"      # fuzzy LLM proposal (low trust, never overwrites structured)
    NORMALIZE_FAILED = "normalize_failed"  # value could not be normalized -> abstained to null


class ParseStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"


# --------------------------------------------------------------------------- #
# Write side
# --------------------------------------------------------------------------- #
class Claim(BaseModel):
    """A single asserted (field, value) pair from one source, with full lineage.

    `value` holds the *normalized* value once it has passed through the normalize
    stage; `raw_value` preserves the original so any transformation is explainable.
    """

    field: str
    value: Any
    raw_value: Any = None
    source: str
    method: str
    normalizer: str | None = None
    normalized_ok: bool = True
    raw_span: str | None = None
    extracted_confidence: float = 0.7

    def with_value(self, value: Any, *, normalizer: str | None, ok: bool, method: str | None = None) -> Claim:
        """Return a copy carrying a normalized value while preserving the original."""
        return self.model_copy(
            update={
                "value": value,
                "raw_value": self.value if self.raw_value is None else self.raw_value,
                "normalizer": normalizer,
                "normalized_ok": ok,
                "method": method or self.method,
            }
        )


class SourceRecord(BaseModel):
    """All claims about one candidate, from one source record (e.g. one CSV row)."""

    source: str
    record_id: str
    claims: list[Claim] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Read side — canonical profile (matches the brief's default output schema)
# --------------------------------------------------------------------------- #
class Location(BaseModel):
    city: str | None = None
    region: str | None = None
    country: str | None = None  # ISO-3166 alpha-2


class Links(BaseModel):
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: list[str] = Field(default_factory=list)


class Skill(BaseModel):
    name: str            # canonical skill name
    # Defaulted so the projection layer can strip confidence (include_confidence=False)
    # and the result still validates against this model.
    confidence: float = 0.0
    sources: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    company: str | None = None
    title: str | None = None
    start: str | None = None  # YYYY-MM
    end: str | None = None    # YYYY-MM or None (current)
    summary: str | None = None


class Education(BaseModel):
    institution: str | None = None
    degree: str | None = None
    field: str | None = None
    end_year: int | None = None


class ProvenanceEntry(BaseModel):
    field: str
    source: str
    method: str


class CanonicalProfile(BaseModel):
    """The golden record. 1:1 with the default output schema."""

    candidate_id: str
    full_name: str | None = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)
    headline: str | None = None
    years_experience: float | None = None
    skills: list[Skill] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    overall_confidence: float = 0.0

    # Internal — per-field confidence used by the projection layer's
    # include_confidence toggle. Excluded from the default JSON dump.
    field_confidence: dict[str, float] = Field(default_factory=dict, exclude=True)


# --------------------------------------------------------------------------- #
# Runtime config — the "required twist". This is DATA fed to a generic projector,
# never logic in the merge engine.
# --------------------------------------------------------------------------- #
class FieldSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str                                   # output key (may be nested via dots)
    from_: str | None = Field(default=None, alias="from")  # canonical source path
    type: str | None = None                     # expected output type for validation
    required: bool = False
    normalize: str | None = None                # normalizer to apply during projection


class OutputConfig(BaseModel):
    """If `fields` is None the default canonical schema is emitted."""

    model_config = ConfigDict(populate_by_name=True)

    fields: list[FieldSpec] | None = None
    include_provenance: bool = True
    include_confidence: bool = True
    on_missing: str = "null"  # one of: null | omit | error
