from datetime import datetime

from claims_agent.domain import MADRID, Claim, LossWindow, apply_update
from claims_agent.engine.templates import (
    display_plate,
    format_loss_window,
    read_back,
    spoken_plate,
    spoken_policy_number,
    spoken_reference,
)
from tests.engine.fakes import COMPLETE, HAPPY_POLICY, NOW


def window(start: datetime, end: datetime | None = None) -> LossWindow:
    return LossWindow(start=start, end=end or start)


def test_identifiers_are_spelled_out_for_speech():
    assert spoken_policy_number("AUT-48213907") == "A U T, 4 8 2 1, 3 9 0 7"
    assert spoken_plate("4721KLM") == "4 7 2 1, K L M"
    assert spoken_reference("SIN-2026-000123") == "S I N, 2026, 0 0 0 1 2 3"


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


def test_multi_day_loss_window_is_shown_in_madrid_time():
    start = datetime.fromisoformat("2026-10-13T20:00+00:00")
    end = datetime(2026, 10, 14, 8, 0, tzinfo=MADRID)
    assert format_loss_window(window(start, end)) == (
        "entre el 13 de octubre de 2026 a las 22:00 y el 14 de octubre de 2026 a las 08:00"
    )


def test_read_back_ends_the_free_text_description_with_a_full_stop():
    claim = apply_update(Claim(), COMPLETE, policy=HAPPY_POLICY, now=NOW).claim
    assert "Descripción: Una piedra que saltó de un camión rompió el parabrisas.\n" in (
        read_back(claim).text
    )
    already_punctuated = claim.model_copy(update={"description": "Me dieron un golpe."})
    assert "golpe.\n" in read_back(already_punctuated).text
