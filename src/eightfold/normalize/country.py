"""Country name -> ISO-3166 alpha-2. Unknown countries abstain to None."""

from __future__ import annotations

from typing import Any

import pycountry

# Common informal spellings pycountry's strict lookup misses.
_ALIASES = {
    "usa": "US", "u.s.a.": "US", "u.s.": "US", "us": "US", "united states of america": "US",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "england": "GB",
    "uae": "AE", "south korea": "KR", "north korea": "KP", "russia": "RU",
}

_ALPHA2 = {c.alpha_2 for c in pycountry.countries}


def to_iso3166(value: Any) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, True
    s = str(value).strip()
    if len(s) == 2 and s.upper() in _ALPHA2:
        return s.upper(), True
    alias = _ALIASES.get(s.lower())
    if alias:
        return alias, True
    try:
        return pycountry.countries.lookup(s).alpha_2, True
    except LookupError:
        return None, False
