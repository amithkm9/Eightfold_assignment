"""Skill string -> canonical skill name.

A small, deliberately swappable synonym map (a real system would back this with a
skills ontology / embedding match — see README "descoped"). Unknown skills fall
back to a cleaned title-case form rather than being dropped: a skill name is rarely
"wrong", just non-canonical, so abstaining here would lose real signal.
"""

from __future__ import annotations

from typing import Any

# canonical name -> set of lowercase aliases
_ONTOLOGY = {
    "Python": {"python", "py", "python3"},
    "Go": {"go", "golang"},
    "JavaScript": {"javascript", "js", "ecmascript"},
    "TypeScript": {"typescript", "ts"},
    "Java": {"java"},
    "Kubernetes": {"kubernetes", "k8s"},
    "Docker": {"docker", "dockerfile"},
    "PostgreSQL": {"postgresql", "postgres", "psql"},
    "AWS": {"aws", "amazon web services"},
    "Distributed Systems": {"distributed systems", "distributed-systems"},
    "Spring": {"spring", "spring boot", "springboot"},
    "Shell": {"shell", "bash", "sh"},
    "Leadership": {"leadership", "team leadership", "people management"},
}

_LOOKUP = {alias: canon for canon, aliases in _ONTOLOGY.items() for alias in aliases}

# Lower-case forms of common all-caps acronyms, so "aws"/"sql" come back uppercased
# without forcing every short word (e.g. "vue", "git") to all-caps.
_ACRONYMS = {"aws", "gcp", "sql", "php", "css", "html", "api", "ml", "ai", "ci",
             "cd", "qa", "etl", "nlp", "sdk", "cli", "ui", "ux", "ios", "tcp", "http"}


def to_canonical(value: Any) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, True
    key = str(value).strip().lower()
    if key in _LOOKUP:
        return _LOOKUP[key], True
    # Fallback: clean and lightly normalize case rather than drop the skill.
    cleaned = " ".join(str(value).split()).strip(" .,-")
    if not cleaned:
        return None, True
    if cleaned.isupper():                  # already an acronym in the input (AWS, SQL)
        return cleaned, True
    if cleaned.lower() in _ACRONYMS:       # known acronym typed in lower/mixed case
        return cleaned.upper(), True
    return cleaned[:1].upper() + cleaned[1:], True
