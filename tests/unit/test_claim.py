import pytest
from pydantic import ValidationError

from claims_agent.domain import (
    Claim,
    ClaimStatus,
    ClosureReason,
    CoverageIssue,
    IncompleteClaimError,
    LossType,
    PendingField,
    Urgency,
    apply_update,
    format_reference,
)
from claims_agent.domain.claim import CONDITIONAL_FIELDS, COVERAGE_ISSUES
from tests.support import COMPLETE, HAPPY_POLICY, NOW


def test_a_new_claim_asks_for_the_identification_first_and_for_no_conditional_field():
    fields = Claim().required_fields
    # The policy is looked up with these two, and the greeting asks for them.
    assert fields[:2] == ("policy_number", "plate")
    assert not set(fields) & set(CONDITIONAL_FIELDS)


def test_driver_name_is_required_right_after_when_driver_is_not_policyholder():
    fields = Claim(driver_is_policyholder=False).required_fields
    assert fields[fields.index("driver_is_policyholder") + 1] == "driver_name"
    assert "driver_name" not in Claim(driver_is_policyholder=True).required_fields


def test_collision_requires_other_vehicle_details_only_if_there_was_one():
    assert "other_vehicle_involved" in Claim(loss_type=LossType.COLLISION).required_fields
    with_other = Claim(loss_type=LossType.COLLISION, other_vehicle_involved=True).required_fields
    assert {"other_vehicle_plate", "friendly_report_signed"} <= set(with_other)
    alone = Claim(loss_type=LossType.COLLISION, other_vehicle_involved=False).required_fields
    unknown = Claim(loss_type=LossType.COLLISION).required_fields
    for fields in (alone, unknown):
        assert not {"other_vehicle_plate", "friendly_report_signed"} & set(fields)


def test_theft_requires_police_report():
    assert "police_report_filed" in Claim(loss_type=LossType.THEFT).required_fields
    assert "police_report_filed" not in Claim(loss_type=LossType.BROKEN_GLASS).required_fields


@pytest.mark.parametrize(
    ("injuries", "immobilized", "expected"),
    [
        (True, None, Urgency.HIGH),
        (True, False, Urgency.HIGH),
        (False, True, Urgency.MEDIUM),
        (None, True, Urgency.MEDIUM),
        (False, False, Urgency.LOW),
        (False, None, None),
        (None, None, None),
    ],
)
def test_urgency_is_derived_from_injuries_and_immobilized_vehicle(injuries, immobilized, expected):
    assert Claim(injuries=injuries, vehicle_immobilized=immobilized).urgency == expected


def test_reference_format():
    assert format_reference(2026, 123) == "SIN-2026-000123"


def test_a_complete_claim_keeps_only_the_values_that_apply():
    claim = apply_update(Claim(), COMPLETE, policy=HAPPY_POLICY, now=NOW).claim
    stale = claim.model_copy(update={"driver_name": "Pedro Ruiz", "other_vehicle_plate": "1234BCD"})

    complete = stale.complete()

    assert complete.plate == claim.plate
    assert complete.driver_name is None
    assert complete.other_vehicle_plate is None
    assert complete.urgency is Urgency.LOW


def test_an_incomplete_claim_says_what_it_is_missing():
    with pytest.raises(IncompleteClaimError, match="missing policy_number, plate, declarant_name"):
        Claim().complete()


# --- states the lifecycle never produces ----------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        {"status": ClaimStatus.CONFIRMED},
        {"reference": "SIN-2026-000001"},
        {"status": ClaimStatus.PENDING_CONFIRMATION, "reference": "SIN-2026-000001"},
        {"status": ClaimStatus.INCOMPLETE},
        {"closure_reason": ClosureReason.ABANDONED},
        {
            "status": ClaimStatus.CONFIRMED,
            "reference": "SIN-2026-000001",
            "closure_reason": ClosureReason.ABANDONED,
        },
        {"pending_fields": [{"field": "plate"}]},
        {"status": ClaimStatus.PENDING_CONFIRMATION, "coverage_issue": "outside_spain"},
        {
            "status": ClaimStatus.CONFIRMED,
            "reference": "SIN-2026-000001",
            "coverage_issue": "outside_spain",
        },
    ],
)
def test_a_status_that_contradicts_the_claim_data_is_rejected(state):
    with pytest.raises(ValidationError):
        Claim.model_validate(state)


@pytest.mark.parametrize(
    "claim",
    [
        Claim(),
        Claim(coverage_issue=CoverageIssue.OUTSIDE_SPAIN),
        Claim(status=ClaimStatus.CONFIRMED, reference="SIN-2026-000001"),
        Claim(
            status=ClaimStatus.NOT_COVERED,
            closure_reason=ClosureReason.OUTSIDE_SPAIN,
            coverage_issue=CoverageIssue.OUTSIDE_SPAIN,
            pending_fields=(PendingField(field="plate"),),
        ),
        Claim(status=ClaimStatus.INCOMPLETE, closure_reason=ClosureReason.ABANDONED),
    ],
)
def test_the_states_the_lifecycle_produces_survive_a_round_trip_through_json(claim):
    assert Claim.model_validate_json(claim.model_dump_json()) == claim


def test_a_coverage_issue_is_stored_with_the_spelling_of_its_closure_reason():
    # Conversations stored before the issue had its own type must still load the same.
    for issue, outcome in COVERAGE_ISSUES.items():
        assert issue.value == outcome.reason.value
        claim = Claim.model_validate({"coverage_issue": outcome.reason.value})
        assert claim.coverage_issue is issue
