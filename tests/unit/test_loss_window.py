from datetime import date, datetime

import pytest

from claims_agent.domain import MADRID, Coverage, LossWindow, Policy, coverage
from claims_agent.domain.errors import FieldError
from claims_agent.domain.loss_window import parse_loss_window

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=MADRID)


def test_start_only_collapses_to_an_instant():
    window = parse_loss_window({"start": "2026-10-19T20:00:00+02:00"}, NOW)
    assert window.start == window.end


@pytest.mark.parametrize("end", [None, "", "  "])
def test_blank_end_collapses_to_an_instant(end):
    # The tool schema requires `end`, so models send it empty when only the start is known.
    window = parse_loss_window({"start": "2026-10-19T20:00", "end": end}, NOW)
    assert window.start == window.end


def test_datetime_without_offset_is_madrid_time():
    window = parse_loss_window({"start": "2026-10-19T20:00:00"}, NOW)
    assert window.start == datetime(2026, 10, 19, 20, 0, tzinfo=MADRID)


def test_end_later_than_now_is_clamped_to_now():
    window = parse_loss_window({"start": "2026-10-20T00:00", "end": "2026-10-20T23:59"}, NOW)
    assert window.end == NOW


def test_future_loss_is_rejected():
    with pytest.raises(FieldError, match="future"):
        parse_loss_window({"start": "2026-10-21T10:00"}, NOW)


def test_end_before_start_is_rejected():
    with pytest.raises(FieldError, match="earlier"):
        parse_loss_window({"start": "2026-10-19T10:00", "end": "2026-10-18T10:00"}, NOW)


@pytest.mark.parametrize("raw", [None, "ayer", {"end": "2026-10-19T10:00"}, {"start": "ayer"}])
def test_malformed_window_is_rejected(raw):
    with pytest.raises(FieldError):
        parse_loss_window(raw, NOW)


POLICY = Policy(
    number="AUT-00000001",
    holder_name="Ana",
    plate="1234BCD",
    start_date=date(2026, 1, 1),
    end_date=date(2026, 12, 31),
)


def _window(start: str, end: str) -> LossWindow:
    return LossWindow(
        start=datetime.fromisoformat(start).replace(tzinfo=MADRID),
        end=datetime.fromisoformat(end).replace(tzinfo=MADRID),
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-01-01T00:00", "2026-01-01T00:00", Coverage.COVERED),
        ("2026-12-31T23:59", "2026-12-31T23:59", Coverage.COVERED),
        ("2025-12-31T23:59", "2025-12-31T23:59", Coverage.NOT_COVERED),
        ("2025-12-30T10:00", "2025-12-31T10:00", Coverage.NOT_COVERED),
        ("2025-12-31T22:00", "2026-01-01T08:00", Coverage.PARTIAL),
    ],
)
def test_coverage_against_inclusive_coverage_period(start, end, expected):
    assert coverage(_window(start, end), POLICY) is expected
