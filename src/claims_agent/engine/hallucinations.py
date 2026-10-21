"""Cheap regex checks on LLM replies for things only code may say (the reference, the
confirmation, coverage, money) and for identifiers nobody gave. They detect, never block:
findings become a Langfuse score and a WARNING log."""

import re
from collections.abc import Sequence

from claims_agent.domain import Claim

_MONEY = ("mentions money", re.compile(r"€|\d\s*(euros?|eur)\b", re.IGNORECASE))
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mentions a claim reference", re.compile(r"\bSIN\s*-?\s*\d", re.IGNORECASE)),
    ("says the claim is confirmed", re.compile(r"\bconfirmad[ao]s?\b", re.IGNORECASE)),
    ("talks about coverage", re.compile(r"\bcubiert[ao]s?\b|\bcobertura\b", re.IGNORECASE)),
    _MONEY,
)
_POLICY_NUMBER = re.compile(r"\bAUT[\s-]*(\d[\d\s-]{6,}\d)", re.IGNORECASE)
_PLATE = re.compile(r"\b(\d{4})[\s-]*([BCDFGHJKLMNPRSTVWXYZ]{3})\b", re.IGNORECASE)


def _policy_numbers(text: str) -> set[str]:
    return {"AUT-" + re.sub(r"\D", "", m.group(1)) for m in _POLICY_NUMBER.finditer(text)}


def _plates(text: str) -> set[str]:
    return {(m.group(1) + m.group(2)).upper() for m in _PLATE.finditer(text)}


def check_reply(reply: str, claim: Claim, declarant_said: str = "") -> list[str]:
    """Findings for `reply`. Identifiers the declarant said themselves, or that were validated
    into the claim, are not findings."""
    findings = [finding for finding, pattern in _RULES if pattern.search(reply)]
    return findings + _unknown_identifiers(reply, claim, declarant_said)


def check_summary(key_points: Sequence[str], claim: Claim, declarant_said: str = "") -> list[str]:
    """Findings for the key points of a confirmed claim's summary, which may well mention the
    confirmation and the reference, but not money or identifiers nobody gave."""
    text = "\n".join(key_points)
    finding, money = _MONEY
    return ([finding] if money.search(text) else []) + _unknown_identifiers(
        text, claim, declarant_said
    )


def _unknown_identifiers(text: str, claim: Claim, declarant_said: str) -> list[str]:
    findings = []
    validated = {v for v in (claim.policy_number, claim.plate, claim.other_vehicle_plate) if v}
    known_numbers = _policy_numbers(declarant_said) | validated
    for number in sorted(_policy_numbers(text) - known_numbers):
        findings.append(f"policy number {number} was never given")

    known_plates = _plates(declarant_said) | validated
    for plate in sorted(_plates(text) - known_plates):
        findings.append(f"plate {plate} was never given")
    return findings
