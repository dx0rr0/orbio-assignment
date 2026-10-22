"""Applying proposed field values to a claim and moving it between statuses.

This is the only place a claim changes: the LLM proposes values, these functions decide what
is valid, when the claim is complete, and when it closes (ADR-0002).
"""

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from claims_agent.domain.claim import (
    CLAIM_FIELDS,
    COVERAGE_ISSUES,
    Claim,
    ClaimStatus,
    ClosureReason,
    LossType,
    PendingField,
)
from claims_agent.domain.errors import ClaimClosedError, FieldError, InvalidTransitionError
from claims_agent.domain.identifiers import (
    normalize_other_plate,
    normalize_plate,
    normalize_policy_number,
)
from claims_agent.domain.location import parse_loss_location
from claims_agent.domain.loss_window import Coverage, coverage, parse_loss_window
from claims_agent.domain.policy import Policy

MAX_FAILED_ATTEMPTS = 3


@dataclass(frozen=True)
class UpdateResult:
    claim: Claim
    accepted: tuple[str, ...]
    rejected: Mapping[str, str]


def _bool(raw: object) -> bool:
    if not isinstance(raw, bool):
        raise FieldError("must be true or false")
    return raw


def _text(min_length: int) -> Callable[[object], str]:
    def parse(raw: object) -> str:
        if not isinstance(raw, str) or len(raw.strip()) < min_length:
            raise FieldError(f"must be a text of at least {min_length} characters")
        return raw.strip()

    return parse


def _loss_type(raw: object) -> LossType:
    if isinstance(raw, str) and raw in LossType:
        return LossType(raw)
    raise FieldError(f"must be one of: {', '.join(LossType)}")


_PARSERS: dict[str, Callable[[object], object]] = {
    "policy_number": normalize_policy_number,
    "plate": normalize_plate,
    "declarant_name": _text(2),
    "driver_is_policyholder": _bool,
    "driver_name": _text(2),
    "loss_type": _loss_type,
    "loss_location": parse_loss_location,
    "description": _text(10),
    "injuries": _bool,
    "vehicle_immobilized": _bool,
    "other_vehicle_involved": _bool,
    "other_vehicle_plate": normalize_other_plate,
    "friendly_report_signed": _bool,
    "police_report_filed": _bool,
}


def _parse(name: str, raw: object, now: datetime) -> object:
    if name == "loss_window":
        return parse_loss_window(raw, now)
    return _PARSERS[name](raw)


def policy_number_to_look_up(claim: Claim, proposed: Mapping[str, object]) -> str | None:
    """The policy number `apply_update` will need the policy for, so the caller can fetch it."""
    if "policy_number" in proposed:
        try:
            return normalize_policy_number(proposed["policy_number"])
        except FieldError:
            pass
    return claim.policy_number


def _inapplicable_fields(claim: Claim) -> set[str]:
    """Conditional fields whose condition is known to be false. While a condition is still
    unknown its fields are kept, so a value mentioned early isn't lost."""
    fields = set()
    if claim.driver_is_policyholder is True:
        fields.add("driver_name")
    if claim.loss_type is not None and claim.loss_type is not LossType.COLLISION:
        fields |= {"other_vehicle_involved", "other_vehicle_plate", "friendly_report_signed"}
    if claim.other_vehicle_involved is False:
        fields |= {"other_vehicle_plate", "friendly_report_signed"}
    if claim.loss_type is not None and claim.loss_type is not LossType.THEFT:
        fields.add("police_report_filed")
    return fields


def apply_update(
    claim: Claim,
    proposed: Mapping[str, object],
    *,
    policy: Policy | None,
    now: datetime,
    already_failed: Collection[str] = (),
) -> UpdateResult:
    """Validate `proposed` field values and apply the valid ones.

    `policy` must be the policy for `policy_number_to_look_up(claim, proposed)`, or None if
    there is no such policy. `now` must be timezone-aware. A failure of a field in
    `already_failed` is recorded but not counted again: attempts are counted per declarant
    answer, and the LLM may send one answer several times.
    """
    if claim.status.is_final:
        raise ClaimClosedError(f"claim is {claim.status}")

    values: dict[str, object] = {}
    errors: dict[str, str] = {}
    for name, raw in proposed.items():
        if name not in CLAIM_FIELDS:
            errors[name] = "unknown field"
            continue
        try:
            values[name] = _parse(name, raw, now)
        except FieldError as error:
            errors[name] = str(error)

    if "policy_number" in values and (policy is None or policy.number != values["policy_number"]):
        errors["policy_number"] = f"there is no policy {values.pop('policy_number')}"
    policy_number = values.get("policy_number", claim.policy_number)
    known_policy = policy if policy is not None and policy.number == policy_number else None

    if known_policy is not None:
        mismatch = f"does not match the plate on policy {known_policy.number}"
        if "plate" in values and values["plate"] != known_policy.plate:
            del values["plate"]
            errors["plate"] = mismatch
        elif (
            "plate" not in proposed
            and "policy_number" in values
            and claim.plate not in (None, known_policy.plate)
        ):
            # A plate accepted before the policy was known turns out not to belong to it.
            values["plate"] = None
            errors["plate"] = mismatch

    accepted = tuple(name for name, value in values.items() if value is not None)
    field_errors = {k: v for k, v in claim.field_errors.items() if k not in accepted}
    failed_attempts = {k: v for k, v in claim.failed_attempts.items() if k not in accepted}
    for name, message in errors.items():
        if name in CLAIM_FIELDS:
            field_errors[name] = message
            if name not in already_failed:
                failed_attempts[name] = failed_attempts.get(name, 0) + 1

    updated = claim.model_copy(
        update=values | {"field_errors": field_errors, "failed_attempts": failed_attempts}
    )
    inapplicable = _inapplicable_fields(updated)
    updated = updated.model_copy(
        update=dict.fromkeys(inapplicable)
        | {
            "field_errors": {k: v for k, v in field_errors.items() if k not in inapplicable},
            "failed_attempts": {k: v for k, v in failed_attempts.items() if k not in inapplicable},
        }
    )

    return UpdateResult(
        claim=_next_status(updated, identified_policy(updated, known_policy)),
        accepted=accepted,
        rejected=errors,
    )


def identified_policy(claim: Claim, policy: Policy | None) -> Policy | None:
    """`policy` if the claim's policy number and plate both match it, else None. Until then the
    declarant is not identified, and nothing from the policy may be used or disclosed."""
    if policy is None or claim.plate is None:
        return None
    if (claim.policy_number, claim.plate) != (policy.number, policy.plate):
        return None
    return policy


def _coverage_issue(claim: Claim, policy: Policy | None) -> ClosureReason | None:
    if claim.loss_location is not None and not claim.loss_location.in_spain:
        return ClosureReason.OUTSIDE_SPAIN
    if claim.loss_window is not None and policy is not None:
        match coverage(claim.loss_window, policy):
            case Coverage.NOT_COVERED:
                return ClosureReason.OUTSIDE_COVERAGE_PERIOD
            case Coverage.PARTIAL:
                return ClosureReason.PARTIAL_COVERAGE
    return None


def _next_status(claim: Claim, policy: Policy | None) -> Claim:
    """A correction while pending confirmation keeps the claim pending if it is still complete
    (the caller reads back what changed) and returns it to in_progress otherwise. A value that would
    leave the loss uncovered keeps the claim in_progress until the declarant confirms it
    (`confirm_value`) or corrects it."""
    if any(n >= MAX_FAILED_ATTEMPTS for n in claim.failed_attempts.values()):
        return close(claim, ClaimStatus.ESCALATED, ClosureReason.RETRIES_EXHAUSTED)
    issue = _coverage_issue(claim, policy)
    if issue is not None:
        status = ClaimStatus.IN_PROGRESS
    else:
        status = (
            ClaimStatus.IN_PROGRESS if claim.missing_fields else ClaimStatus.PENDING_CONFIRMATION
        )
    return claim.model_copy(update={"status": status, "coverage_issue": issue})


def confirm_value(claim: Claim) -> Claim:
    """The declarant confirmed the value that leaves the loss uncovered: the claim closes."""
    if claim.status.is_final or claim.coverage_issue is None:
        raise InvalidTransitionError("there is no value waiting for confirmation")
    _, status = COVERAGE_ISSUES[claim.coverage_issue]
    return close(claim, status, claim.coverage_issue)


def close(claim: Claim, status: ClaimStatus, reason: ClosureReason) -> Claim:
    """Close the claim in a final status other than confirmed, recording what was missing."""
    if claim.status.is_final:
        raise ClaimClosedError(f"claim is {claim.status}")
    if not status.is_final or status is ClaimStatus.CONFIRMED:
        raise InvalidTransitionError(f"cannot close a claim as {status}")
    pending = tuple(
        PendingField(field=name, last_error=claim.field_errors.get(name))
        for name in claim.missing_fields
    )
    return claim.model_copy(
        update={"status": status, "closure_reason": reason, "pending_fields": pending}
    )


def confirm(claim: Claim, reference: str) -> Claim:
    """The declarant approved the read-back of the complete claim."""
    if claim.status is not ClaimStatus.PENDING_CONFIRMATION:
        raise InvalidTransitionError(f"cannot confirm a claim that is {claim.status}")
    return claim.model_copy(update={"status": ClaimStatus.CONFIRMED, "reference": reference})
