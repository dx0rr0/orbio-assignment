"""Cases of the time-to-first-audio eval: one declarant turn each.

A case is the conversation as it stands before the turn (fields already collected, and what
the agent just said), a recorded utterance, and the checks the claim must pass after the turn.
Dates in the checks are relative to the moment the case runs, as the declarant's are.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from claims_agent.domain import MADRID, Claim, ClaimStatus, LossType
from claims_agent.engine import templates

# A check gets the claim after the turn, the texts the agent said and the moment the turn ran,
# and returns whether it holds.
Check = Callable[[Claim, list[str], datetime], bool]


@dataclass(frozen=True)
class Case:
    id: str
    audio: str
    tags: tuple[str, ...]
    # Field values collected before the turn, in the form the update_claim tool takes them.
    known: Mapping[str, Any]
    # What the agent said last, before the declarant's turn (None: built from `known`, for the
    # read-back of a complete claim).
    asked: str | None
    checks: Mapping[str, Check]
    # Earlier exchanges, oldest first, as (role, text) with role "user" or "assistant".
    history: tuple[tuple[str, str], ...] = field(default=())


# --- check helpers ----------------------------------------------------------------------------


def equals(name: str, expected: Any) -> Check:
    return lambda claim, replies, now: getattr(claim, name) == expected


def text_has(name: str, fragment: str) -> Check:
    def check(claim: Claim, replies: list[str], now: datetime) -> bool:
        value = getattr(claim, name)
        return isinstance(value, str) and fragment.casefold() in value.casefold()

    return check


def location(
    *, municipality: str | None = None, province: str, way_has: str | None = None
) -> Check:
    def check(claim: Claim, replies: list[str], now: datetime) -> bool:
        loc = claim.loss_location
        if loc is None or loc.province != province:
            return False
        if municipality is not None and loc.municipality.casefold() != municipality.casefold():
            return False
        return way_has is None or (loc.way is not None and way_has.casefold() in loc.way.casefold())

    return check


def starts_near(days_ago: int, at: time, tolerance: timedelta = timedelta(minutes=45)) -> Check:
    def check(claim: Claim, replies: list[str], now: datetime) -> bool:
        if claim.loss_window is None:
            return False
        day = now.astimezone(MADRID).date() - timedelta(days=days_ago)
        expected = datetime.combine(day, at, MADRID)
        return abs(claim.loss_window.start - expected) <= tolerance

    return check


def window_days(start: Callable[[date], date], end: Callable[[date], date]) -> Check:
    """The loss window starts and ends on the given days (computed from today)."""

    def check(claim: Claim, replies: list[str], now: datetime) -> bool:
        if claim.loss_window is None:
            return False
        today = now.astimezone(MADRID).date()
        return claim.loss_window.start.astimezone(MADRID).date() == start(
            today
        ) and claim.loss_window.end.astimezone(MADRID).date() == end(today)

    return check


def last_weekday(weekday: int, *, weeks_back: int = 0) -> Callable[[date], date]:
    """The most recent `weekday` (0 = Monday) before today, `weeks_back` weeks earlier."""

    def day(today: date) -> date:
        delta = (today.weekday() - weekday) % 7 or 7
        return today - timedelta(days=delta + 7 * weeks_back)

    return day


def previous_week(weekday: int) -> Callable[[date], date]:
    """`weekday` (0 = Monday) of the calendar week before this one."""

    def day(today: date) -> date:
        return today - timedelta(days=today.weekday() + 7 - weekday)

    return day


def status(*expected: ClaimStatus) -> Check:
    return lambda claim, replies, now: claim.status in expected


def said(fragment: str) -> Check:
    return lambda claim, replies, now: any(fragment in reply for reply in replies)


def unchanged(*names: str) -> Check:
    """None of these fields got a value in the turn."""
    return lambda claim, replies, now: all(getattr(claim, name) is None for name in names)


# --- states the cases start from --------------------------------------------------------------

MARIA = {
    "policy_number": "AUT-48213907",
    "plate": "4721KLM",
    "declarant_name": "María López Fernández",
}
MARIA_DRIVING = MARIA | {"driver_is_policyholder": True}
MARIA_GLASS = MARIA_DRIVING | {
    "loss_type": "broken_glass",
    "loss_window": {"start": "__yesterday_19__"},
}
MARIA_COMPLETE = MARIA_GLASS | {
    "loss_location": {
        "way": "A-3, a la altura de Arganda del Rey",
        "municipality": "Arganda del Rey",
        "province": "Madrid",
        "country": "España",
    },
    "description": "Una piedra que saltó de un camión rompió el parabrisas.",
    "injuries": False,
    "vehicle_immobilized": False,
}
CARLOS_THEFT = {
    "policy_number": "AUT-73058416",
    "plate": "6614LPR",
    "declarant_name": "Carlos Moreno Díaz",
    "loss_type": "theft",
}

GREETING = templates.greeting().text

CASES: tuple[Case, ...] = (
    Case(
        id="1a",
        audio="1a.wav",
        tags=("identification",),
        known={},
        asked=GREETING,
        checks={
            "policy": equals("policy_number", "AUT-48213907"),
            "plate": equals("plate", "4721KLM"),
            "name": text_has("declarant_name", "María"),
        },
    ),
    Case(
        id="1b",
        audio="1b.wav",
        tags=("loss",),
        known=MARIA,
        asked="Gracias, María. ¿Conducía usted el vehículo? ¿Qué ocurrió y cuándo?",
        checks={
            "driver": equals("driver_is_policyholder", True),
            "type": equals("loss_type", LossType.BROKEN_GLASS),
            "when": starts_near(1, time(19, 0), timedelta(minutes=90)),
            "where": location(municipality="Arganda del Rey", province="Madrid"),
            "injuries": equals("injuries", False),
            "immobilized": equals("vehicle_immobilized", False),
            # Every field is given, so the claim goes to the read-back.
            "pending": status(ClaimStatus.PENDING_CONFIRMATION),
        },
    ),
    Case(
        id="3a",
        audio="3a.wav",
        tags=("identification",),
        known={},
        asked=GREETING,
        checks={
            "policy": equals("policy_number", "AUT-73058416"),
            "plate": equals("plate", "6614LPR"),
            "name": text_has("declarant_name", "Carlos"),
            "type": equals("loss_type", LossType.THEFT),
        },
    ),
    Case(
        id="3b",
        audio="3b.wav",
        tags=("loss",),
        known=CARLOS_THEFT | {"driver_is_policyholder": True},
        asked="Entiendo. ¿Cuándo y dónde vio el coche por última vez, y cuándo se dio cuenta "
        "de que no estaba?",
        checks={
            "when": window_days(previous_week(1), previous_week(2)),
            "where": location(municipality="Alcalá de Henares", province="Madrid"),
            "report": equals("police_report_filed", True),
            "closed": status(ClaimStatus.NOT_COVERED),
        },
    ),
    Case(
        id="R01",
        audio="R01.wav",
        tags=("identification", "spelled"),
        known={},
        asked=GREETING,
        checks={
            "policy": equals("policy_number", "AUT-62947105"),
            "plate": equals("plate", "2196FDS"),
            "name": text_has("declarant_name", "Lucía"),
        },
    ),
    Case(
        id="R02",
        audio="R02.wav",
        tags=("identification", "spelled"),
        known={},
        asked="¿Me puede dar su número de póliza?",
        checks={"policy": equals("policy_number", "AUT-48213907")},
    ),
    Case(
        id="R03",
        audio="R03.wav",
        tags=("identification", "spelled"),
        known={"policy_number": "AUT-48213907"},
        asked="Gracias. ¿Y la matrícula del vehículo?",
        checks={"plate": equals("plate", "4721KLM")},
    ),
    Case(
        id="R04",
        audio="R04.wav",
        tags=("identification", "short"),
        known={"policy_number": "AUT-48213907", "plate": "4721KLM"},
        asked="Perfecto. ¿Me dice su nombre completo?",
        checks={"name": equals("declarant_name", "María López Fernández")},
    ),
    Case(
        id="R05",
        audio="R05.wav",
        tags=("driver", "short"),
        known=MARIA,
        asked="¿Conducía usted el vehículo cuando ocurrió?",
        checks={
            "driver": equals("driver_is_policyholder", True),
            "nothing_new": unchanged(
                "driver_name",
                "loss_type",
                "loss_window",
                "loss_location",
                "description",
                "injuries",
                "vehicle_immobilized",
                "other_vehicle_involved",
                "police_report_filed",
            ),
        },
    ),
    Case(
        id="R06",
        audio="R06.wav",
        tags=("driver",),
        known=MARIA,
        asked="¿Conducía usted el vehículo cuando ocurrió?",
        checks={
            "driver": equals("driver_is_policyholder", False),
            "driver_name": text_has("driver_name", "Pablo"),
        },
    ),
    Case(
        id="R07",
        audio="R07.wav",
        tags=("loss",),
        known=MARIA_DRIVING,
        asked="¿Qué ocurrió y cuándo?",
        checks={
            "type": equals("loss_type", LossType.BROKEN_GLASS),
            "when": starts_near(2, time(10, 30)),
        },
    ),
    Case(
        id="R08",
        audio="R08.wav",
        tags=("location",),
        known=MARIA_GLASS,
        asked="¿Dónde ocurrió?",
        checks={"where": location(municipality="Valencia", province="Valencia", way_has="Puerto")},
    ),
    Case(
        id="R09",
        audio="R09.wav",
        tags=("location",),
        known=MARIA_GLASS,
        asked="¿Dónde ocurrió?",
        checks={"where": location(province="Castellón", way_has="AP-7")},
    ),
    Case(
        id="R10",
        audio="R10.wav",
        tags=("location", "short"),
        known=MARIA_GLASS,
        asked="¿Recuerda el número de la avenida del Puerto?",
        history=(
            ("assistant", "¿Dónde ocurrió?"),
            ("user", "En Valencia, en la avenida del Puerto."),
        ),
        checks={"where": location(municipality="Valencia", province="Valencia", way_has="8")},
    ),
    Case(
        id="R11",
        audio="R11.wav",
        tags=("damage",),
        known={
            k: v for k, v in MARIA_COMPLETE.items() if k not in ("injuries", "vehicle_immobilized")
        },
        asked="¿Hubo algún herido?",
        checks={
            "injuries": equals("injuries", True),
            "immobilized": equals("vehicle_immobilized", True),
            "112": said("112"),
        },
    ),
    Case(
        id="R12",
        audio="R12.wav",
        tags=("confirmation",),
        known=MARIA_COMPLETE,
        asked=None,
        checks={
            "when": starts_near(1, time(20, 30), timedelta(minutes=20)),
            "pending": status(ClaimStatus.PENDING_CONFIRMATION),
        },
    ),
    Case(
        id="R13",
        audio="R13.wav",
        tags=("off_topic",),
        known=MARIA_GLASS,
        asked="¿Dónde ocurrió?",
        checks={
            "nothing_new": unchanged("loss_location", "description", "injuries"),
            "open": status(ClaimStatus.IN_PROGRESS),
        },
    ),
    Case(
        id="R14",
        audio="R14.wav",
        tags=("driver", "refusal"),
        known=MARIA | {"driver_is_policyholder": False},
        asked="¿Cómo se llama la persona que conducía?",
        checks={
            "no_name": unchanged("driver_name"),
            "open": status(ClaimStatus.IN_PROGRESS),
        },
    ),
    Case(
        id="R15",
        audio="R15.wav",
        tags=("other_vehicle", "spelled"),
        known=MARIA_DRIVING
        | {
            "loss_type": "collision",
            "loss_window": {"start": "__yesterday_19__"},
            "other_vehicle_involved": True,
        },
        asked="¿Cuál es la matrícula del otro vehículo?",
        checks={"other_plate": equals("other_vehicle_plate", "2548MNP")},
    ),
    Case(
        id="R16",
        audio="R16.wav",
        tags=("loss",),
        known=MARIA_DRIVING,
        asked="¿Qué ocurrió?",
        checks={
            "type": equals("loss_type", LossType.THEFT),
            "when": window_days(last_weekday(4), last_weekday(5)),
            "where": location(municipality="Madrid", province="Madrid", way_has="Alcalá"),
            "report": equals("police_report_filed", False),
        },
    ),
)


# Held out from the hill-climb rounds, one per kind of case, and run only to validate the
# result: rounds are measured on the rest (train), so a change can't be fitted to these.
TEST_IDS = frozenset({"1b", "3a", "R03", "R07", "R10", "R12", "R16"})
assert {case.id for case in CASES} >= TEST_IDS


def resolve(known: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """`known` with its relative placeholders turned into dates for `now`."""
    yesterday_19 = datetime.combine(
        now.astimezone(MADRID).date() - timedelta(days=1), time(19, 0), MADRID
    )
    resolved = dict(known)
    window = resolved.get("loss_window")
    if window == {"start": "__yesterday_19__"}:
        resolved["loss_window"] = {"start": yesterday_19.isoformat()}
    return resolved


def read_back_text(claim: Claim) -> str:
    return templates.read_back(claim).text
