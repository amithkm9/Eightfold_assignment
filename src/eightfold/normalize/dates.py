"""Dates -> YYYY-MM (or YYYY when only the year is known).

We never invent a month. If the month is genuinely unknown we keep just the year
rather than fabricating "-01". Unparseable values abstain to None.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from dateutil import parser as dtparser

YYYY_MM = re.compile(r"^\d{4}-\d{2}$")  # public: the canonical YYYY-MM shape
_YYYY = re.compile(r"^\d{4}$")
_PRESENT = {"present", "current", "now", "to date", "ongoing"}
# An explicit month token: a word-bounded month name/abbrev, or a numeric MM next to the
# year. Word boundaries stop substrings ("Maryland", "Mayfield") from looking like months,
# and the negative lookahead stops a year range ("2018-2020") from looking like YYYY-MM.
_MONTH_TOKEN = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    r"|\b\d{1,2}[/\-.]\d{2,4}\b"
    r"|\b\d{4}[/\-.]\d{1,2}(?!\d)",
    re.IGNORECASE,
)


def to_year_month(value: Any) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, True
    s = str(value).strip()
    if s.lower() in _PRESENT:
        return None, True  # open-ended end date -> null
    if YYYY_MM.match(s):
        if 1 <= int(s[5:7]) <= 12:
            return s, True
        return s[:4], True  # invalid month (e.g. "2019-13") -> keep year, never fabricate
    if _YYYY.match(s):
        return s, True  # year-only: keep year, do NOT fabricate a month
    try:
        dt = dtparser.parse(s, default=datetime(1900, 1, 1), fuzzy=True)
    except (ValueError, OverflowError):
        return None, False
    year_match = re.search(r"(19|20)\d{2}", s)
    if not year_match:
        return None, False
    # Only emit a month when the text actually contains a month token; otherwise keep
    # the year alone — never fabricate a month from dateutil's January default
    # (e.g. "Summer 2019", "circa 2015").
    if _MONTH_TOKEN.search(s):
        return f"{dt.year:04d}-{dt.month:02d}", True
    return year_match.group(0), True


def to_year(value: Any) -> tuple[int | None, bool]:
    """For education end_year (an int)."""
    if value in (None, ""):
        return None, True
    s = str(value).strip()
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return int(m.group(0)), True
    return None, False
