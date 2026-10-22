"""Cheap regex checks on LLM replies for things only code may say (the reference, the
confirmation, coverage, money) and for identifiers nobody gave. They detect, never block:
findings become a Langfuse score and a WARNING log."""

import logging
import re
from collections.abc import Sequence

from claims_agent.domain import Claim, Conversation, Role
from claims_agent.domain.identifiers import PLATE_DIGITS, PLATE_LETTERS, POLICY_NUMBER_PREFIX
from claims_agent.ports import Telemetry

logger = logging.getLogger(__name__)

_MONEY = ("mentions money", re.compile(r"€|\d\s*(euros?|eur)\b", re.IGNORECASE))
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mentions a claim reference", re.compile(r"\bSIN\s*-?\s*\d", re.IGNORECASE)),
    ("says the claim is confirmed", re.compile(r"\bconfirmad[ao]s?\b", re.IGNORECASE)),
    ("talks about coverage", re.compile(r"\bcubiert[ao]s?\b|\bcobertura\b", re.IGNORECASE)),
    _MONEY,
)
# Looser than the identifiers: an LLM may write them with any separators or case, and a policy
# number of the wrong length counts too.
_POLICY_NUMBER = re.compile(rf"\b{POLICY_NUMBER_PREFIX}[\s-]*(\d[\d\s-]{{6,}}\d)", re.IGNORECASE)
_PLATE = re.compile(rf"\b({PLATE_DIGITS})[\s-]*({PLATE_LETTERS})\b", re.IGNORECASE)


def _policy_numbers(text: str) -> set[str]:
    return {
        f"{POLICY_NUMBER_PREFIX}-" + re.sub(r"\D", "", m.group(1))
        for m in _POLICY_NUMBER.finditer(text)
    }


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
    validated = {v for v in (claim.policy_number, claim.plate, claim.other_vehicle_plate) if v}
    known_numbers = _policy_numbers(declarant_said) | validated
    known_plates = _plates(declarant_said) | validated
    return [
        *(
            f"policy number {number} was never given"
            for number in sorted(_policy_numbers(text) - known_numbers)
        ),
        *(f"plate {plate} was never given" for plate in sorted(_plates(text) - known_plates)),
    ]


def report_reply(reply: str, conversation: Conversation, telemetry: Telemetry) -> None:
    """Scores the checks of an LLM reply in `conversation`, and warns of any finding."""
    findings = check_reply(reply, conversation.claim, _declarant_said(conversation))
    _report(findings, conversation, telemetry, score="hallucination_check", of="LLM reply")


def report_summary(
    key_points: Sequence[str], conversation: Conversation, telemetry: Telemetry
) -> None:
    """Scores the checks of the key points of `conversation`'s summary, and warns of any
    finding."""
    findings = check_summary(key_points, conversation.claim, _declarant_said(conversation))
    _report(
        findings,
        conversation,
        telemetry,
        score="summary_hallucination_check",
        of="the claim summary",
    )


def _declarant_said(conversation: Conversation) -> str:
    return " ".join(m.content for m in conversation.messages if m.role is Role.USER and m.content)


def _report(
    findings: Sequence[str],
    conversation: Conversation,
    telemetry: Telemetry,
    *,
    score: str,
    of: str,
) -> None:
    comment = "; ".join(findings) or None
    telemetry.score(score, 0.0 if findings else 1.0, comment)
    if comment:
        logger.warning(
            "Possible hallucination in %s: %s",
            of,
            comment,
            extra={"conversation_id": str(conversation.id)},
        )
