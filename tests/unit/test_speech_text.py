import pytest

from claims_agent.engine.speech_text import spell_identifiers


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Póliza AUT-48213907.", "Póliza A, U, T, 48, 21, 39, 07."),
        ("su póliza AUT48213907 y", "su póliza A, U, T, 48, 21, 39, 07 y"),
        ("matrícula 4721 KLM.", "matrícula 47, 21, K, L, M."),
        ("matrícula 4721KLM.", "matrícula 47, 21, K, L, M."),
        ("referencia SIN-2026-000123.", "referencia S, I, N, 2026, 00, 01, 23."),
    ],
)
def test_identifiers_are_spelled_out_for_speech(text: str, spoken: str):
    assert spell_identifiers(text) == spoken


def test_other_numbers_are_left_for_the_tts_to_read():
    text = "El 21 de octubre de 2026 a las 09:00, en la calle Alcalá 1234."
    assert spell_identifiers(text) == text
