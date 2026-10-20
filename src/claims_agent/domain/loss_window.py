"""When the loss happened, as an interval, and how it relates to a policy's coverage period."""

from datetime import date, datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict

from claims_agent.domain.errors import FieldError
from claims_agent.domain.policy import Policy

MADRID = ZoneInfo("Europe/Madrid")


class LossWindow(BaseModel):
    """`start == end` when the declarant knows when it happened; for a theft or parked-car
    damage it runs from when the vehicle was last seen to when the damage was discovered."""

    model_config = ConfigDict(frozen=True)

    start: AwareDatetime
    end: AwareDatetime


class Coverage(StrEnum):
    COVERED = "covered"
    NOT_COVERED = "not_covered"
    PARTIAL = "partial"


def _parse_datetime(raw: object, name: str) -> datetime:
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            raise FieldError(f"{name} must be an ISO 8601 datetime") from None
    else:
        raise FieldError(f"{name} must be an ISO 8601 datetime")
    # Declarants speak in local time, so a datetime without offset is Madrid time.
    return value if value.tzinfo else value.replace(tzinfo=MADRID)


def parse_loss_window(raw: object, now: datetime) -> LossWindow:
    """Parse `{"start": iso, "end": iso?}`. `end` defaults to `start`; an `end` later than
    `now` is clamped to `now` (e.g. "some time today")."""
    if not isinstance(raw, dict) or "start" not in raw:
        raise FieldError('must be an object {"start": datetime, "end": datetime}')
    start = _parse_datetime(raw["start"], "start")
    end = _parse_datetime(raw["end"], "end") if raw.get("end") is not None else start
    if start > now:
        raise FieldError("the loss cannot be in the future")
    if end < start:
        raise FieldError("end cannot be earlier than start")
    return LossWindow(start=start, end=min(end, now))


def _period_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min, MADRID), datetime.combine(end, time.max, MADRID)


def coverage(window: LossWindow, policy: Policy) -> Coverage:
    period_start, period_end = _period_bounds(policy.start_date, policy.end_date)
    if window.start >= period_start and window.end <= period_end:
        return Coverage.COVERED
    if window.end < period_start or window.start > period_end:
        return Coverage.NOT_COVERED
    return Coverage.PARTIAL
