"""Cheap regex checks on LLM replies for things only code may say (the reference, the
confirmation, coverage, money) and for identifiers nobody gave. They detect, never block:
findings become a Langfuse score and a WARNING log."""

import re

from claims_agent.domain import Claim

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mentions a claim reference", re.compile(r"\bSIN\s*-?\s*\d", re.IGNORECASE)),
    ("says the claim is confirmed", re.compile(r"\bconfirmad[ao]s?\b", re.IGNORECASE)),
    ("talks about coverage", re.compile(r"\bcubiert[ao]s?\b|\bcobertura\b", re.IGNORECASE)),
    ("mentions money", re.compile(r"€|\d\s*(euros?|eur)\b", re.IGNORECASE)),
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

    validated = {v for v in (claim.policy_number, claim.plate, claim.other_vehicle_plate) if v}
    known_numbers = _policy_numbers(declarant_said) | validated
    for number in sorted(_policy_numbers(reply) - known_numbers):
        findings.append(f"policy number {number} was never given")

    known_plates = _plates(declarant_said) | validated
    for plate in sorted(_plates(reply) - known_plates):
        findings.append(f"plate {plate} was never given")
    return findings
