"""Normalization of identifiers that arrive from speech: policy numbers and plates.

Transcriptions render the same identifier in many ways ("AUT-1234 5678", "aut 12345678",
"1234-bcd"), so every parser strips separators and case before matching.
"""

import re

from claims_agent.domain.errors import FieldError

_POLICY_NUMBER = re.compile(r"^AUT(\d{8})$")
# Current Spanish plates: 4 digits + 3 consonants (no vowels, Ñ or Q).
_PLATE = re.compile(r"^\d{4}[BCDFGHJKLMNPRSTVWXYZ]{3}$")
# Other vehicles may carry older provincial or foreign plates, so only sanity-check them.
_OTHER_PLATE = re.compile(r"^[0-9A-Z]{4,10}$")


def _compact(raw: object) -> str:
    if not isinstance(raw, str):
        raise FieldError("must be a string")
    return re.sub(r"[^0-9A-Za-z]", "", raw).upper()


def normalize_policy_number(raw: object) -> str:
    match = _POLICY_NUMBER.match(_compact(raw))
    if not match:
        raise FieldError("policy numbers look like AUT-12345678 (AUT followed by 8 digits)")
    return f"AUT-{match.group(1)}"


def normalize_plate(raw: object) -> str:
    plate = _compact(raw)
    if not _PLATE.match(plate):
        raise FieldError("Spanish plates are 4 digits followed by 3 consonants, e.g. 1234 BCD")
    return plate


def normalize_other_plate(raw: object) -> str:
    plate = _compact(raw)
    if not _OTHER_PLATE.match(plate):
        raise FieldError("a plate must have between 4 and 10 letters or digits")
    return plate
