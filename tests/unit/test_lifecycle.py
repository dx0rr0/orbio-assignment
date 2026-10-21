from datetime import date, datetime

import pytest

from claims_agent.domain import (
    MADRID,
    MAX_FAILED_ATTEMPTS,
    Claim,
    ClaimClosedError,
    ClaimStatus,
    ClosureReason,
    InvalidTransitionError,
    LossType,
    PendingField,
    Policy,
    apply_update,
    close,
    confirm,
    policy_number_to_look_up,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=MADRID)
POLICY = Policy(
    number="AUT-00000001",
    holder_name="Ana García",
    plate="1234BCD",
    start_date=date(2026, 1, 1),
    end_date=date(2026, 12, 31),
)
OTHER_POLICY = POLICY.model_copy(update={"number": "AUT-00000002", "plate": "5678FGH"})

IDENTIFIED = {"policy_number": "AUT-00000001", "plate": "1234 BCD"}
COMPLETE = IDENTIFIED | {
    "declarant_name": "Ana García",
    "driver_is_policyholder": True,
    "loss_type": "broken_glass",
    "loss_window": {"start": "2026-10-19T08:00"},
    "loss_location": {"municipality": "Valencia", "province": "Valencia", "country": "España"},
    "description": "Una piedra rompió el parabrisas en la autovía",
    "injuries": False,
    "vehicle_immobilized": False,
}


def update(claim=None, policy=POLICY, **proposed):
    return apply_update(claim or Claim(), proposed, policy=policy, now=NOW)


def complete_claim() -> Claim:
    return update(**COMPLETE).claim


# --- accepting and rejecting values ---------------------------------------------------------


def test_valid_values_are_accepted_and_normalized():
    result = update(policy_number="aut 0000 0001", plate="1234-bcd")
    assert result.accepted == ("policy_number", "plate")
    assert result.rejected == {}
    assert result.claim.policy_number == "AUT-00000001"
    assert result.claim.plate == "1234BCD"


def test_invalid_value_is_not_stored_and_its_error_is_recorded():
    result = update(injuries="sí")
    assert result.claim.injuries is None
    assert result.rejected == {"injuries": "must be true or false"}
    assert result.claim.field_errors == {"injuries": "must be true or false"}
    assert result.claim.failed_attempts == {"injuries": 1}


def test_unknown_field_is_rejected_but_not_tracked():
    result = update(favourite_colour="blue")
    assert result.rejected == {"favourite_colour": "unknown field"}
    assert result.claim.failed_attempts == {}


def test_accepting_a_field_clears_its_previous_error():
    claim = update(injuries="sí").claim
    claim = update(claim, injuries=True).claim
    assert claim.field_errors == {}
    assert claim.failed_attempts == {}


def test_nonexistent_policy_is_rejected():
    result = update(policy=None, policy_number="AUT-99999999")
    assert result.claim.policy_number is None
    assert "there is no policy AUT-99999999" in result.rejected["policy_number"]


def test_policy_passed_for_a_different_number_does_not_validate_it():
    result = update(policy=OTHER_POLICY, policy_number="AUT-00000001")
    assert "policy_number" in result.rejected


def test_plate_must_match_the_policy():
    result = update(policy_number="AUT-00000001", plate="9999 BCD")
    assert result.claim.policy_number == "AUT-00000001"
    assert result.claim.plate is None
    assert "does not match" in result.rejected["plate"]


def test_plate_accepted_before_the_policy_is_dropped_if_it_does_not_belong_to_it():
    claim = update(policy=None, plate="9999 BCD").claim
    assert claim.plate == "9999BCD"
    result = update(claim, policy_number="AUT-00000001")
    assert result.claim.plate is None
    assert "does not match" in result.claim.field_errors["plate"]


def test_policy_number_to_look_up_prefers_the_proposed_one():
    claim = Claim(policy_number="AUT-00000001")
    assert policy_number_to_look_up(claim, {"policy_number": "aut-00000002"}) == "AUT-00000002"
    assert policy_number_to_look_up(claim, {"policy_number": "nope"}) == "AUT-00000001"
    assert policy_number_to_look_up(claim, {}) == "AUT-00000001"


# --- conditional fields ---------------------------------------------------------------------


def test_fields_whose_condition_turns_false_are_dropped():
    claim = update(
        loss_type="collision", other_vehicle_involved=True, other_vehicle_plate="V1234AB"
    ).claim
    claim = update(claim, loss_type="theft").claim
    assert claim.other_vehicle_involved is None
    assert claim.other_vehicle_plate is None


def test_conditional_field_mentioned_before_its_condition_is_kept():
    claim = update(driver_name="Pedro García").claim
    assert claim.driver_name == "Pedro García"
    claim = update(claim, driver_is_policyholder=False).claim
    assert claim.driver_name == "Pedro García"
    assert "driver_name" not in claim.missing_fields


# --- status transitions ---------------------------------------------------------------------


def test_claim_with_missing_fields_stays_in_progress():
    claim = update(policy_number="AUT-00000001").claim
    assert claim.status is ClaimStatus.IN_PROGRESS


def test_complete_claim_is_pending_confirmation():
    assert complete_claim().status is ClaimStatus.PENDING_CONFIRMATION


def test_correction_that_keeps_the_claim_complete_stays_pending():
    claim = update(complete_claim(), description="Una piedra rompió la luna trasera").claim
    assert claim.status is ClaimStatus.PENDING_CONFIRMATION


def test_correction_that_opens_a_new_required_field_goes_back_to_in_progress():
    claim = update(complete_claim(), loss_type="theft").claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    assert claim.missing_fields == ("police_report_filed",)


def test_loss_abroad_is_not_covered():
    claim = update(
        loss_location={"municipality": "Oporto", "province": "Porto", "country": "Portugal"}
    ).claim
    assert claim.status is ClaimStatus.NOT_COVERED
    assert claim.closure_reason is ClosureReason.OUTSIDE_SPAIN


def test_loss_outside_the_coverage_period_is_not_covered():
    claim = update(**IDENTIFIED, loss_window={"start": "2025-06-01T10:00"}).claim
    assert claim.status is ClaimStatus.NOT_COVERED
    assert claim.closure_reason is ClosureReason.OUTSIDE_COVERAGE_PERIOD


def test_coverage_is_checked_when_the_policy_arrives_after_the_window():
    claim = update(policy=None, loss_window={"start": "2025-06-01T10:00"}).claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    claim = update(claim, **IDENTIFIED).claim
    assert claim.status is ClaimStatus.NOT_COVERED


def test_coverage_is_not_checked_until_the_plate_matches_the_policy():
    # The coverage period is policy data: it says nothing until the declarant is identified.
    window = {"start": "2025-06-01T10:00"}
    claim = update(policy_number="AUT-00000001", plate="9999 ZZZ", loss_window=window).claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    claim = update(policy_number="AUT-00000001", loss_window=window).claim
    assert claim.status is ClaimStatus.IN_PROGRESS
    claim = update(claim, plate="1234 BCD").claim
    assert claim.status is ClaimStatus.NOT_COVERED


def test_window_partly_outside_the_coverage_period_is_escalated():
    claim = update(
        **IDENTIFIED,
        loss_type="theft",
        loss_window={"start": "2025-12-31T20:00", "end": "2026-01-01T09:00"},
    ).claim
    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.PARTIAL_COVERAGE


def test_repeated_failures_on_a_field_escalate_the_claim():
    claim = Claim()
    for _ in range(MAX_FAILED_ATTEMPTS):
        claim = update(claim, plate="no la sé").claim
    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.RETRIES_EXHAUSTED
    assert PendingField(field="plate", last_error=claim.field_errors["plate"]) in (
        claim.pending_fields
    )


def test_closed_claim_cannot_be_updated():
    claim = update(
        loss_location={"municipality": "Oporto", "province": "Porto", "country": "Portugal"}
    ).claim
    with pytest.raises(ClaimClosedError):
        update(claim, injuries=False)


# --- closing and confirming -----------------------------------------------------------------


def test_close_as_incomplete_records_reason_and_pending_fields():
    claim = update(policy_number="AUT-00000001", injuries="sí").claim
    closed = close(claim, ClaimStatus.INCOMPLETE, ClosureReason.DATA_REFUSED)
    assert closed.status is ClaimStatus.INCOMPLETE
    assert closed.closure_reason is ClosureReason.DATA_REFUSED
    assert closed.policy_number == "AUT-00000001"
    assert PendingField(field="injuries", last_error="must be true or false") in (
        closed.pending_fields
    )
    assert PendingField(field="plate") in closed.pending_fields


@pytest.mark.parametrize("status", [ClaimStatus.CONFIRMED, ClaimStatus.IN_PROGRESS])
def test_close_only_into_non_confirmed_final_statuses(status):
    with pytest.raises(InvalidTransitionError):
        close(Claim(), status, ClosureReason.ABANDONED)


def test_confirm_assigns_reference():
    claim = confirm(complete_claim(), "SIN-2026-000001")
    assert claim.status is ClaimStatus.CONFIRMED
    assert claim.reference == "SIN-2026-000001"


def test_cannot_confirm_an_incomplete_claim():
    with pytest.raises(InvalidTransitionError):
        confirm(Claim(), "SIN-2026-000001")


def test_complete_claim_scenario_with_collision_and_other_driver():
    claim = update(
        **COMPLETE
        | {
            "loss_type": "collision",
            "driver_is_policyholder": False,
            "vehicle_immobilized": True,
        }
    ).claim
    assert claim.missing_fields == ("driver_name", "other_vehicle_involved")
    claim = update(
        claim,
        driver_name="Pedro García",
        other_vehicle_involved=True,
        other_vehicle_plate="5678 FGH",
        friendly_report_signed=True,
    ).claim
    assert claim.status is ClaimStatus.PENDING_CONFIRMATION
    assert claim.loss_type is LossType.COLLISION
    assert claim.urgency == "medium"


def test_a_failure_already_counted_is_recorded_but_not_counted_again():
    claim = update(plate="no la sé").claim
    result = apply_update(
        claim, {"plate": "tampoco"}, policy=None, now=NOW, already_failed={"plate"}
    )
    assert result.claim.failed_attempts == {"plate": 1}
    assert result.rejected.keys() == {"plate"}
