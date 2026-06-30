"""ATS JSON blob — semi-structured, with its OWN field names that do NOT match ours.

The whole point of this source is *schema remapping*: the ATS calls things
`applicant`, `contact.phoneNumbers`, `tech_stack`, `work_history`. We map those
foreign paths onto our canonical fields (method = json_remap). Anything we cannot
confidently map is left out — never guessed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Claim, Method, SourceKind, SourceRecord
from .base import Source

_CONF = 0.85  # structured but foreign-mapped -> slightly below direct CSV


def _dig(obj: Any, *paths: str) -> Any:
    """Return the first present value among several dotted candidate paths."""
    for path in paths:
        cur: Any = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class ATSJsonSource(Source):
    kind = SourceKind.ATS_JSON.value

    def _extract(self, path: Path) -> list[SourceRecord]:
        data = json.loads(path.read_text(encoding="utf-8"))
        # The blob may be a single applicant, a list, or wrapped under a key.
        if isinstance(data, dict):
            applicants = _dig(data, "candidates", "applicants", "records") or [data]
        else:
            applicants = data
        applicants = _as_list(applicants)

        records: list[SourceRecord] = []
        for i, app in enumerate(applicants):
            if not isinstance(app, dict):
                continue
            try:
                claims = self._claims_for(app)
            except Exception:  # noqa: BLE001 - one malformed applicant must not drop the rest
                continue
            if claims:
                records.append(SourceRecord(source=self.kind,
                                            record_id=f"{self.kind}:applicant{i}", claims=claims))
        return records

    def _claims_for(self, app: dict) -> list[Claim]:
        claims: list[Claim] = []

        def add(field: str, value: Any, span: str | None = None, method: str = Method.JSON_REMAP.value):
            claims.append(Claim(field=field, value=value, source=self.kind, method=method,
                                raw_span=span if span is not None else str(value),
                                extracted_confidence=_CONF))

        # ----- name (foreign: name / personal.{first,last}) -----
        name = _dig(app, "full_name", "name", "applicant_name")
        if not name:
            first = _dig(app, "personal.first", "personal.firstName", "first_name")
            last = _dig(app, "personal.last", "personal.lastName", "last_name")
            name = " ".join(p for p in [first, last] if p) or None
        if name:
            add("full_name", name)

        # ----- emails (foreign: contact.emails / emails / email) -----
        for e in _as_list(_dig(app, "contact.emails", "emails", "email", "contact.email")):
            if e:
                add("emails", e)

        # ----- phones (foreign: contact.phoneNumbers / phones / phone) -----
        for p in _as_list(_dig(app, "contact.phoneNumbers", "contact.phones", "phones", "phone")):
            if p:
                add("phones", p)

        # ----- location (foreign: loc.{town,state,nation}) -----
        loc = {
            "city": _dig(app, "loc.town", "location.city", "city", "address.city"),
            "region": _dig(app, "loc.state", "location.region", "region", "state", "address.state"),
            "country": _dig(app, "loc.nation", "location.country", "country", "address.country"),
        }
        if any(loc.values()):
            add("location", loc, span=", ".join(v for v in loc.values() if v))

        # ----- headline / title -----
        headline = _dig(app, "headline", "current_title", "title")
        if headline:
            add("headline", headline)

        # ----- skills (foreign: tech_stack / skills) -----
        for s in _as_list(_dig(app, "tech_stack", "skills", "skillset")):
            if isinstance(s, dict):
                s = s.get("name") or s.get("skill")
            if s:
                add("skills", s)

        # ----- experience (foreign: work_history [{org, role, from, to}]) -----
        for w in _as_list(_dig(app, "work_history", "experience", "positions")):
            if not isinstance(w, dict):
                continue
            exp = {
                "company": _dig(w, "org", "company", "employer"),
                "title": _dig(w, "role", "title", "position"),
                "start": _dig(w, "from", "start", "start_date"),
                "end": _dig(w, "to", "end", "end_date"),
                "summary": _dig(w, "summary", "description"),
            }
            if any(v for k, v in exp.items()):
                add("experience", exp, span=f"{exp.get('title','')} @ {exp.get('company','')}")

        # ----- education (foreign: schools) -----
        for ed in _as_list(_dig(app, "schools", "education", "degrees")):
            if not isinstance(ed, dict):
                continue
            edu = {
                "institution": _dig(ed, "school", "institution", "name"),
                "degree": _dig(ed, "degree", "qualification"),
                "field": _dig(ed, "field", "major", "field_of_study"),
                "end_year": _dig(ed, "grad_year", "end_year", "year"),
            }
            if any(v for v in edu.values()):
                add("education", edu)

        return claims
