"""The claim the agent collects, its statuses and the rules derived from its data."""

import hashlib
from enum import StrEnum
from typing import overload

from pydantic import BaseModel, ConfigDict

from claims_agent.domain.errors import IncompleteClaimError
from claims_agent.domain.location import LossLocation
from claims_agent.domain.loss_window import LossWindow


class LossType(StrEnum):
    COLLISION = "collision"
    THEFT = "theft"
    BROKEN_GLASS = "broken_glass"
    PARKED_DAMAGE = "parked_damage"
    OTHER = "other"


class ClaimStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    INCOMPLETE = "incomplete"
    NOT_COVERED = "not_covered"
    ESCALATED = "escalated"

    @property
    def is_final(self) -> bool:
        return self not in (ClaimStatus.IN_PROGRESS, ClaimStatus.PENDING_CONFIRMATION)


class ClosureReason(StrEnum):
    DATA_REFUSED = "data_refused"
    ABANDONED = "abandoned"
    RETRIES_EXHAUSTED = "retries_exhausted"
    SYSTEM_ERROR = "system_error"
    OUTSIDE_COVERAGE_PERIOD = "outside_coverage_period"
    PARTIAL_COVERAGE = "partial_coverage"
    OUTSIDE_SPAIN = "outside_spain"


# The field whose value raises each coverage issue, and the status the claim closes in once the
# declarant confirms that value.
COVERAGE_ISSUES: dict[ClosureReason, tuple[str, ClaimStatus]] = {
    ClosureReason.OUTSIDE_SPAIN: ("loss_location", ClaimStatus.NOT_COVERED),
    ClosureReason.OUTSIDE_COVERAGE_PERIOD: ("loss_window", ClaimStatus.NOT_COVERED),
    ClosureReason.PARTIAL_COVERAGE: ("loss_window", ClaimStatus.ESCALATED),
}


class Urgency(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PendingField(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    last_error: str | None = None


# Fields every claim needs, in the order the agent should ask for them.
BASE_FIELDS: tuple[str, ...] = (
    "policy_number",
    "plate",
    "declarant_name",
    "driver_is_policyholder",
    "loss_type",
    "loss_window",
    "loss_location",
    "description",
    "injuries",
    "vehicle_immobilized",
)
CONDITIONAL_FIELDS: tuple[str, ...] = (
    "driver_name",
    "other_vehicle_involved",
    "other_vehicle_plate",
    "friendly_report_signed",
    "police_report_filed",
)
CLAIM_FIELDS: tuple[str, ...] = BASE_FIELDS + CONDITIONAL_FIELDS


@overload
def _urgency(*, injuries: bool, vehicle_immobilized: bool) -> Urgency: ...
@overload
def _urgency(*, injuries: bool | None, vehicle_immobilized: bool | None) -> Urgency | None: ...
def _urgency(*, injuries: bool | None, vehicle_immobilized: bool | None) -> Urgency | None:
    if injuries:
        return Urgency.HIGH
    if vehicle_immobilized:
        return Urgency.MEDIUM
    if injuries is False and vehicle_immobilized is False:
        return Urgency.LOW
    return None


class CompleteClaim(BaseModel):
    """The values of a claim with no missing field, typed as present. A conditional field is
    set exactly when it applies (e.g. `other_vehicle_plate` only in a collision with another
    vehicle). Built by `Claim.complete`."""

    model_config = ConfigDict(frozen=True)

    policy_number: str
    plate: str
    declarant_name: str
    driver_is_policyholder: bool
    driver_name: str | None = None
    loss_type: LossType
    loss_window: LossWindow
    loss_location: LossLocation
    description: str
    injuries: bool
    vehicle_immobilized: bool
    other_vehicle_involved: bool | None = None
    other_vehicle_plate: str | None = None
    friendly_report_signed: bool | None = None
    police_report_filed: bool | None = None

    @property
    def urgency(self) -> Urgency:
        return _urgency(injuries=self.injuries, vehicle_immobilized=self.vehicle_immobilized)


class Claim(BaseModel):
    """Only valid values are stored; the last rejection of each field lives in `field_errors`."""

    model_config = ConfigDict(frozen=True)

    status: ClaimStatus = ClaimStatus.IN_PROGRESS

    policy_number: str | None = None
    plate: str | None = None
    declarant_name: str | None = None
    driver_is_policyholder: bool | None = None
    driver_name: str | None = None
    loss_type: LossType | None = None
    loss_window: LossWindow | None = None
    loss_location: LossLocation | None = None
    description: str | None = None
    injuries: bool | None = None
    vehicle_immobilized: bool | None = None
    other_vehicle_involved: bool | None = None
    other_vehicle_plate: str | None = None
    friendly_report_signed: bool | None = None
    police_report_filed: bool | None = None

    field_errors: dict[str, str] = {}
    failed_attempts: dict[str, int] = {}
    closure_reason: ClosureReason | None = None
    # A value that would leave the loss uncovered, waiting for the declarant to confirm it.
    coverage_issue: ClosureReason | None = None
    pending_fields: tuple[PendingField, ...] = ()
    reference: str | None = None

    @property
    def required_fields(self) -> tuple[str, ...]:
        fields = list(BASE_FIELDS)
        if self.driver_is_policyholder is False:
            fields.insert(fields.index("driver_is_policyholder") + 1, "driver_name")
        if self.loss_type is LossType.COLLISION:
            fields.append("other_vehicle_involved")
            if self.other_vehicle_involved:
                fields += ["other_vehicle_plate", "friendly_report_signed"]
        if self.loss_type is LossType.THEFT:
            fields.append("police_report_filed")
        return tuple(fields)

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(f for f in self.required_fields if getattr(self, f) is None)

    def complete(self) -> CompleteClaim:
        """Its required values. Raises `IncompleteClaimError` if any is missing."""
        if self.missing_fields:
            raise IncompleteClaimError(f"missing {', '.join(self.missing_fields)}")
        return CompleteClaim.model_validate({f: getattr(self, f) for f in self.required_fields})

    @property
    def data_fingerprint(self) -> str:
        """Changes whenever a field value changes, and only then."""
        data = self.model_dump_json(include=set(CLAIM_FIELDS))
        return hashlib.sha256(data.encode()).hexdigest()

    @property
    def value_to_confirm(self) -> str | None:
        """The field the declarant must confirm before the claim closes over its coverage."""
        return COVERAGE_ISSUES[self.coverage_issue][0] if self.coverage_issue else None

    @property
    def fingerprint_to_confirm(self) -> str:
        """What the declarant is asked to confirm next: the flagged value if there is one,
        otherwise the whole claim (the read-back)."""
        if self.value_to_confirm is None:
            return self.data_fingerprint
        value = self.model_dump_json(include={"coverage_issue", self.value_to_confirm})
        return hashlib.sha256(value.encode()).hexdigest()

    @property
    def urgency(self) -> Urgency | None:
        """Derived, never asked: None until the data that decides it is known."""
        return _urgency(injuries=self.injuries, vehicle_immobilized=self.vehicle_immobilized)


def format_reference(year: int, sequence: int) -> str:
    return f"SIN-{year}-{sequence:06d}"
