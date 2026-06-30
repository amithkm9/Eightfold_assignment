"""Recruiter CSV export — structured rows: name, email, phone, current_company, title.

Highest-trust source for contact/identity fields: values are read directly from
cells (method = csv_direct), no inference involved.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import Claim, Method, SourceKind, SourceRecord
from .base import Source

# Tolerate common header spellings without inventing data.
_ALIASES = {
    "name": "name", "full_name": "name", "candidate": "name", "candidate_name": "name",
    "email": "email", "email_address": "email", "e-mail": "email",
    "phone": "phone", "phone_number": "phone", "mobile": "phone", "telephone": "phone",
    "current_company": "company", "company": "company", "employer": "company",
    "title": "title", "job_title": "title", "role": "title", "position": "title",
}
_CONF = 0.92  # structured, direct read


def _norm_header(h: str) -> str | None:
    return _ALIASES.get((h or "").strip().lower().replace(" ", "_"))


class RecruiterCSVSource(Source):
    kind = SourceKind.RECRUITER_CSV.value

    def _extract(self, path: Path) -> list[SourceRecord]:
        records: list[SourceRecord] = []
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            header_map = {col: _norm_header(col) for col in (reader.fieldnames or [])}
            for i, row in enumerate(reader):
                try:
                    rec = self._row_to_record(row, header_map, i)
                except Exception:  # noqa: BLE001 - one garbage row must not drop the whole file
                    continue
                if rec is not None:
                    records.append(rec)
        return records

    def _row_to_record(self, row: dict, header_map: dict, i: int) -> SourceRecord | None:
        canon: dict[str, str] = {}
        for col, raw in row.items():
            key = header_map.get(col)
            if key and raw and raw.strip():
                canon[key] = raw.strip()
        claims: list[Claim] = []

        def add(field: str, value: str, span: str) -> None:
            claims.append(Claim(field=field, value=value, source=self.kind,
                                method=Method.CSV_DIRECT.value, raw_span=span,
                                extracted_confidence=_CONF))

        if "name" in canon:
            add("full_name", canon["name"], canon["name"])
        if "email" in canon:
            add("emails", canon["email"], canon["email"])
        if "phone" in canon:
            add("phones", canon["phone"], canon["phone"])
        if "company" in canon or "title" in canon:
            exp = {"company": canon.get("company"), "title": canon.get("title"),
                   "start": None, "end": None, "summary": None}
            claims.append(Claim(field="experience", value=exp, source=self.kind,
                                method=Method.CSV_DIRECT.value,
                                raw_span=f"{canon.get('title', '')} @ {canon.get('company', '')}".strip(" @"),
                                extracted_confidence=_CONF))
        if not claims:
            return None
        return SourceRecord(source=self.kind, record_id=f"{self.kind}:row{i}", claims=claims)
