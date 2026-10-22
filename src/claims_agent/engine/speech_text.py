"""What a text-to-speech engine is given instead of a text: identifiers spelled out, so that
"AUT-48213907" is not read as a large number. Applies to every spoken text, the LLM's replies
too; the text on screen keeps the identifiers as they are."""

import re

from claims_agent.domain.identifiers import (
    PLATE_DIGITS,
    PLATE_LETTERS,
    POLICY_NUMBER_DIGITS,
    POLICY_NUMBER_PREFIX,
)

# Identifiers as the agent writes them: references, policy numbers and current Spanish plates.
_REFERENCE = re.compile(r"\bSIN-(\d{4})-(\d{6})\b")
_POLICY_NUMBER = re.compile(rf"\b{POLICY_NUMBER_PREFIX}[-\s]?({POLICY_NUMBER_DIGITS})\b")
_PLATE = re.compile(rf"\b({PLATE_DIGITS})\s?({PLATE_LETTERS})\b")


def _spell(identifier: str) -> str:
    """Letters one by one and digits in pairs, each apart: 'KLM4721' → 'K, L, M, 47, 21'. A
    pair is a number the TTS reads right in any language; four digits it reads as thousands."""
    pieces: list[str] = []
    for run in re.findall(r"[A-Z]+|\d+", identifier):
        pieces += [run[i : i + 2] for i in range(0, len(run), 2)] if run.isdigit() else list(run)
    return ", ".join(pieces)


def spell_identifiers(text: str) -> str:
    """`text` with its identifiers spelled out for the TTS: 'AUT-48213907' → 'A, U, T, 48, 21,
    39, 07', '4721 KLM' → '47, 21, K, L, M', 'SIN-2026-000123' → 'S, I, N, 2026, 00, 01, 23'
    (the year whole, as a person says it)."""
    text = _REFERENCE.sub(lambda m: f"S, I, N, {m[1]}, {_spell(m[2])}", text)
    text = _POLICY_NUMBER.sub(lambda m: f"{_spell(POLICY_NUMBER_PREFIX)}, {_spell(m[1])}", text)
    return _PLATE.sub(lambda m: _spell(m[1] + m[2]), text)
