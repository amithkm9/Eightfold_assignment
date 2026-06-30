"""Phone -> E.164. Unparseable/invalid numbers abstain to None (honest-null)."""

from __future__ import annotations

from typing import Any

import phonenumbers

# Default region used when a number has no country code. Documented assumption;
# overridable. Numbers that are not valid for the assumed region abstain to None
# rather than being coerced into a wrong-but-confident value.
DEFAULT_REGION = "US"


def to_e164(value: Any, default_region: str = DEFAULT_REGION) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, True
    try:
        num = phonenumbers.parse(str(value), default_region)
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164), True
    except phonenumbers.NumberParseException:
        pass
    return None, False
