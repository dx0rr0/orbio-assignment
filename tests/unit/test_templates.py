from datetime import datetime

import pytest

from claims_agent.domain import MADRID, Claim, IncompleteClaimError, LossWindow, apply_update
from claims_agent.engine.templates import (
    BotMessage,
    confirm_coverage_value,
    display_plate,
    format_loss_window,
    read_back,
    read_back_changes,
    spell_identifiers,
)
from tests.engine.fakes import COMPLETE, HAPPY_POLICY, NOW


def window(start: datetime, end: datetime | None = None) -> LossWindow:
    return LossWindow(start=start, end=end or start)


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


def test_the_llms_replies_are_spoken_with_their_identifiers_spelled_out():
    reply = BotMessage("Su póliza es AUT-48213907 y su matrícula 4721 KLM.", scripted=False)
    assert reply.spoken == "Su póliza es A, U, T, 48, 21, 39, 07 y su matrícula 47, 21, K, L, M."


def test_current_plates_are_displayed_with_a_space_and_others_as_they_are():
    assert display_plate("4721KLM") == "4721 KLM"
    assert display_plate("M1234AB") == "M1234AB"


def test_instant_loss_window():
    assert format_loss_window(window(datetime(2026, 10, 19, 19, 0, tzinfo=MADRID))) == (
        "el 19 de octubre de 2026 a las 19:00"
    )


def test_same_day_loss_window():
    start = datetime(2026, 10, 19, 8, 0, tzinfo=MADRID)
    assert format_loss_window(window(start, start.replace(hour=14))) == (
        "el 19 de octubre de 2026, entre las 08:00 y las 14:00"
    )


def test_a_whole_day_is_read_without_hours():
    start = datetime(2026, 10, 21, 0, 0, tzinfo=MADRID)
    assert format_loss_window(window(start, start.replace(hour=23, minute=59))) == (
        "el 21 de octubre de 2026, sin hora concreta"
    )
    assert format_loss_window(window(start, datetime(2026, 10, 22, 23, 59, tzinfo=MADRID))) == (
        "entre el 21 de octubre de 2026 y el 22 de octubre de 2026, sin hora concreta"
    )


def test_multi_day_loss_window_is_shown_in_madrid_time():
    start = datetime.fromisoformat("2026-10-13T20:00+00:00")
    end = datetime(2026, 10, 14, 8, 0, tzinfo=MADRID)
    assert format_loss_window(window(start, end)) == (
        "entre el 13 de octubre de 2026 a las 22:00 y el 14 de octubre de 2026 a las 08:00"
    )


def test_the_read_back_is_a_short_summary_of_the_loss():
    claim = apply_update(Claim(), COMPLETE, policy=HAPPY_POLICY, now=NOW).claim

    assert read_back(claim).text == (
        "Resumo su declaración: una rotura de lunas, el 19 de octubre de 2026 a las 19:00, "
        "en A-3, Arganda del Rey (Madrid). No hubo heridos y el vehículo puede circular. "
        "¿Es correcto?"
    )


def test_the_read_back_of_a_collision_says_the_other_vehicle_and_the_friendly_report():
    collision = COMPLETE | {
        "loss_type": "collision",
        "loss_location": {"municipality": "Valencia", "province": "Valencia", "country": "España"},
        "injuries": True,
        "vehicle_immobilized": True,
        "other_vehicle_involved": True,
        "other_vehicle_plate": "1234 BCD",
        "friendly_report_signed": False,
    }
    claim = apply_update(Claim(), collision, policy=HAPPY_POLICY, now=NOW).claim

    message = read_back(claim)

    assert message.text == (
        "Resumo su declaración: una colisión, el 19 de octubre de 2026 a las 19:00, "
        "en Valencia. Hubo heridos y el vehículo quedó inmovilizado. "
        "Hubo otro vehículo implicado, matrícula 1234 BCD, y no firmaron parte amistoso. "
        "¿Es correcto?"
    )
    assert "matrícula 12, 34, B, C, D," in message.spoken


def test_the_read_back_of_a_theft_says_whether_it_was_reported():
    theft = COMPLETE | {"loss_type": "theft", "police_report_filed": True}
    claim = apply_update(Claim(), theft, policy=HAPPY_POLICY, now=NOW).claim

    text = read_back(claim).text

    assert text.startswith("Resumo su declaración: un robo,")
    assert "No hubo heridos. Ya ha presentado denuncia. ¿Es correcto?" in text


def test_the_read_back_leaves_out_what_was_checked_or_said_by_the_declarant():
    claim = apply_update(Claim(), COMPLETE, policy=HAPPY_POLICY, now=NOW).claim

    text = read_back(claim).text

    for left_out in ("AUT-", "4721", "María", "piedra"):
        assert left_out not in text


def test_a_corrected_description_ends_with_a_full_stop():
    heard = apply_update(Claim(), COMPLETE, policy=HAPPY_POLICY, now=NOW).claim
    claim = heard.model_copy(update={"description": "Me dieron un golpe"})

    assert "- Descripción: Me dieron un golpe.\n" in read_back_changes(heard, claim).text


def corrected(claim: Claim, update: dict) -> Claim:
    return apply_update(claim, update, policy=HAPPY_POLICY, now=NOW).claim


def test_after_a_correction_only_the_changed_field_is_read_back():
    heard = corrected(Claim(), COMPLETE)
    location = {"way": "A-3", "municipality": "Leganés", "province": "Madrid", "country": "España"}
    claim = corrected(heard, {"loss_location": location})

    assert read_back_changes(heard, claim).text == (
        "He corregido este dato:\n"
        "- Lugar: A-3, Leganés (Madrid).\n"
        "Lo demás queda igual. ¿Es todo correcto?"
    )


def test_several_corrected_fields_are_read_back_in_the_claim_order():
    heard = corrected(Claim(), COMPLETE)
    claim = corrected(
        heard,
        {
            "description": "Una piedra rompió el parabrisas",
            "loss_window": {"start": "2026-10-19T20:00", "end": "2026-10-19T20:00"},
        },
    )

    assert read_back_changes(heard, claim).text == (
        "He corregido estos datos:\n"
        "- Fecha y hora: el 19 de octubre de 2026 a las 20:00.\n"
        "- Descripción: Una piedra rompió el parabrisas.\n"
        "Lo demás queda igual. ¿Es todo correcto?"
    )


def test_a_corrected_identifier_is_spelled_out_for_speech():
    heard = corrected(Claim(), COMPLETE)
    claim = heard.model_copy(update={"plate": "4721KLN"})

    message = read_back_changes(heard, claim)

    assert "- Matrícula: 4721 KLN." in message.text
    assert "Matrícula: 47, 21, K, L, N." in message.spoken


def test_a_corrections_list_is_spoken_without_its_dashes():
    heard = corrected(Claim(), COMPLETE)
    claim = corrected(heard, {"injuries": True, "vehicle_immobilized": True})

    message = read_back_changes(heard, claim)

    assert "\n- Heridos: sí." in message.text
    assert "- " not in message.spoken


def test_a_correction_that_changes_nothing_says_so():
    heard = corrected(Claim(), COMPLETE)

    assert read_back_changes(heard, heard).text == (
        "No ha cambiado ningún dato. ¿Es todo correcto?"
    )


def test_a_loss_abroad_is_stated_back_before_closing():
    abroad = {"municipality": "Oporto", "province": "Porto", "country": "Portugal"}
    claim = apply_update(Claim(), {"loss_location": abroad}, policy=None, now=NOW).claim

    text = confirm_coverage_value(claim, None).text

    assert "Oporto (Portugal)" in text
    assert "¿Es correcto el lugar?" in text


def test_an_incomplete_claim_is_not_read_back():
    with pytest.raises(IncompleteClaimError, match="missing"):
        read_back(Claim(plate="4721KLM"))


def test_without_a_coverage_issue_there_is_no_value_to_confirm():
    claim = apply_update(Claim(), COMPLETE, policy=HAPPY_POLICY, now=NOW).claim

    with pytest.raises(ValueError, match="nothing to confirm"):
        confirm_coverage_value(claim, HAPPY_POLICY)
