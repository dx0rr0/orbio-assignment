import pytest

from claims_agent.domain import Claim
from claims_agent.engine.hallucinations import check_reply, check_summary

VALIDATED = Claim(policy_number="AUT-48213907", plate="4721KLM")


@pytest.mark.parametrize(
    ("reply", "finding"),
    [
        ("Su referencia es SIN-2026-000001.", "mentions a claim reference"),
        ("Su declaración ha quedado confirmada.", "says the claim is confirmed"),
        ("Tranquila, el siniestro está cubierto.", "talks about coverage"),
        ("Le pagaremos unos 300 €.", "mentions money"),
        ("Recibirá 300 euros.", "mentions money"),
    ],
)
def test_things_only_code_may_say_are_flagged(reply, finding):
    assert finding in check_reply(reply, Claim())


def test_ordinary_reply_has_no_findings():
    assert check_reply("Gracias, María. ¿Dónde ocurrió el siniestro?", VALIDATED) == []


def test_validated_identifiers_are_not_findings():
    reply = "He anotado la póliza AUT-48213907 y la matrícula 4721 KLM."
    assert check_reply(reply, VALIDATED) == []


def test_identifiers_the_declarant_said_are_not_findings():
    reply = "La póliza AUT 1111 2222 no aparece. ¿Puede revisarla?"
    assert check_reply(reply, Claim(), declarant_said="mi póliza es AUT-11112222") == []


def test_identifiers_nobody_gave_are_findings():
    findings = check_reply("¿Es la póliza AUT-99998888 con matrícula 1234 BCD?", VALIDATED)
    assert findings == [
        "policy number AUT-99998888 was never given",
        "plate 1234BCD was never given",
    ]


def test_a_summary_may_mention_the_confirmation_but_not_money_or_unknown_plates():
    key_points = ["Declaración confirmada con la referencia SIN-2026-000001.", "Daños de 300 €."]
    assert check_summary(key_points, VALIDATED) == ["mentions money"]
    assert check_summary(["Choque con el 9999 ZZZ."], VALIDATED) == [
        "plate 9999ZZZ was never given"
    ]
