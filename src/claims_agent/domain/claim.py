"""The claim the agent collects, its statuses and the rules derived from its data."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

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

    @property
    def urgency(self) -> Urgency | None:
        """Derived, never asked: None until the data that decides it is known."""
        if self.injuries:
            return Urgency.HIGH
        if self.vehicle_immobilized:
            return Urgency.MEDIUM
        if self.injuries is False and self.vehicle_immobilized is False:
            return Urgency.LOW
        return None


def format_reference(year: int, sequence: int) -> str:
    return f"SIN-{year}-{sequence:06d}"
