"""Recruiter notes (.txt) — free text (unstructured group).

Deterministic, high-precision regex extraction ONLY. We pull the things that have
unambiguous patterns (emails, phones, profile URLs, explicit "Skills:" lines, a
stated years-of-experience). We do NOT try to guess a name out of prose — that is
exactly the kind of confident-but-wrong inference the brief warns against. Fuzzy
enrichment is left to the optional, clearly-tagged LLM layer (eightfold.llm).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Claim, Method, SourceKind, SourceRecord
from .base import Source

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w])(\+?\d[\d\s().\-]{7,}\d)(?![\w])")
_LINKEDIN = re.compile(r"https?://(?:www\.)?linkedin\.com/[^\s,)]+", re.I)
_GITHUB = re.compile(r"https?://(?:www\.)?github\.com/[^\s,)]+", re.I)
_URL = re.compile(r"https?://[^\s,)]+", re.I)
_YEARS = re.compile(r"(\d{1,2})\+?\s*(?:years|yrs)\b", re.I)
_SKILLS_LINE = re.compile(r"(?:skills|tech(?:nologies)?|stack)\s*[:\-]\s*(.+)", re.I)
# Keyword is case-insensitive and word-bounded (so "Name:"/"Candidate:" match but
# "Username:"/"nickname:" don't); the captured name stays capitalized to avoid grabbing
# stray lowercase prose.
_NAME_LABEL = re.compile(
    r"\b(?i:candidate|name)\s*[:\-]\s*([A-Z][A-Za-z.'\-]+(?:[ \t]+[A-Z][A-Za-z.'\-]+){0,3})"
)

_CONF_CONTACT = 0.7   # regex contact match in prose: precise pattern, looser context
_CONF_SOFT = 0.55     # skills / years stated in free text
_CONF_NAME = 0.6      # labelled "Name:/Candidate:" line
_CONF_PORTFOLIO = 0.5  # bare URL, not linkedin/github


class RecruiterNotesSource(Source):
    kind = SourceKind.RECRUITER_NOTES.value

    def _extract(self, path: Path) -> list[SourceRecord]:
        text = path.read_text(encoding="utf-8", errors="replace")
        claims: list[Claim] = []

        def add(field, value, conf, span, method=Method.REGEX.value):
            claims.append(Claim(field=field, value=value, source=self.kind, method=method,
                                raw_span=span, extracted_confidence=conf))

        m = _NAME_LABEL.search(text)
        if m:
            add("full_name", m.group(1).strip(), _CONF_NAME, m.group(0))

        for em in dict.fromkeys(_EMAIL.findall(text)):
            add("emails", em, _CONF_CONTACT, em)

        # Phones: capture, but require enough digits to avoid matching IDs/years.
        for ph in dict.fromkeys(_PHONE.findall(text)):
            if sum(ch.isdigit() for ch in ph) >= 8:
                add("phones", ph.strip(), _CONF_CONTACT, ph.strip())

        for ln in dict.fromkeys(_LINKEDIN.findall(text)):
            add("links.linkedin", ln.rstrip(".,"), _CONF_CONTACT, ln)
        for gh in dict.fromkeys(_GITHUB.findall(text)):
            add("links.github", gh.rstrip(".,"), _CONF_CONTACT, gh)
        for url in dict.fromkeys(_URL.findall(text)):
            if "linkedin.com" in url.lower() or "github.com" in url.lower():
                continue
            add("links.portfolio", url.rstrip(".,"), _CONF_PORTFOLIO, url)

        ym = _YEARS.search(text)
        if ym:
            add("years_experience", int(ym.group(1)), _CONF_SOFT, ym.group(0))

        for sm in _SKILLS_LINE.finditer(text):
            for sk in re.split(r"[,/|;]| and ", sm.group(1)):
                sk = sk.strip().rstrip(".")
                if sk and len(sk) <= 40:
                    add("skills", sk, _CONF_SOFT, sm.group(0))

        if not claims:
            return []
        return [SourceRecord(source=self.kind, record_id=f"{self.kind}:notes", claims=claims)]
