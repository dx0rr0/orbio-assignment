import pytest

from claims_agent.domain import Claim, LossType, Urgency, format_reference


def test_base_claim_requires_base_fields_in_asking_order():
    assert Claim().required_fields == (
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


def test_driver_name_is_required_right_after_when_driver_is_not_policyholder():
    fields = Claim(driver_is_policyholder=False).required_fields
    assert fields[fields.index("driver_is_policyholder") + 1] == "driver_name"
    assert "driver_name" not in Claim(driver_is_policyholder=True).required_fields


def test_collision_requires_other_vehicle_details_only_if_there_was_one():
    assert "other_vehicle_involved" in Claim(loss_type=LossType.COLLISION).required_fields
    with_other = Claim(loss_type=LossType.COLLISION, other_vehicle_involved=True).required_fields
    assert {"other_vehicle_plate", "friendly_report_signed"} <= set(with_other)
    alone = Claim(loss_type=LossType.COLLISION, other_vehicle_involved=False).required_fields
    assert "other_vehicle_plate" not in alone


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
