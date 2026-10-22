"""The panel's Spanish wording must follow the domain's real error messages, so these tests
produce each error through the domain instead of copying its text."""

import pytest

from claims_agent.domain import Claim, apply_update
from claims_service.intake.error_messages import UNTRANSLATED, display_error
from tests.support import HAPPY_POLICY, NOW

SPAIN = {"municipality": "Valencia", "province": "Valencia", "country": "España"}


def rejection(field: str, value, policy=HAPPY_POLICY) -> str:
    result = apply_update(Claim(), {field: value}, policy=policy, now=NOW)
    return result.rejected[field]


@pytest.mark.parametrize(
    ("field", "value", "policy", "spanish"),
    [
        ("policy_number", "AUT-99990000", None, "No existe la póliza AUT-99990000"),
        ("policy_number", "12345", None, "Las pólizas tienen el formato AUT-12345678"),
        ("plate", "12 AB", None, "Las matrículas tienen 4 cifras y 3 consonantes, como 1234 BCD"),
        ("other_vehicle_plate", "25", None, "Una matrícula tiene entre 4 y 10 letras o cifras"),
        (
            "loss_location",
            SPAIN | {"province": "Narnia"},
            None,
            "«Narnia» no es una provincia española",
        ),
        ("loss_window", {"start": "2030-01-01T10:00"}, None, "La fecha no puede ser futura"),
        (
            "loss_window",
            {"start": "2026-10-19T10:00", "end": "2026-10-18T10:00"},
            None,
            "El final no puede ser anterior al inicio",
        ),
    ],
)
def test_errors_about_what_the_declarant_said_are_translated(field, value, policy, spanish):
    assert display_error(rejection(field, value, policy)) == spanish


def test_plate_mismatch_is_translated():
    result = apply_update(
        Claim(),
        {"policy_number": HAPPY_POLICY.number, "plate": "9999 ZZZ"},
        policy=HAPPY_POLICY,
        now=NOW,
    )
    assert display_error(result.rejected["plate"]) == (
        f"No coincide con la matrícula de la póliza {HAPPY_POLICY.number}"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("injuries", "sí"),
        ("loss_window", "ayer"),
        ("loss_location", {"municipality": "Valencia"}),
        ("loss_type", "meteorito"),
    ],
)
def test_malformed_tool_values_get_a_neutral_message(field, value):
    assert display_error(rejection(field, value)) == UNTRANSLATED
