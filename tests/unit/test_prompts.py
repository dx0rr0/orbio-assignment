import json

from claims_agent.domain import Claim, LossType
from claims_agent.engine.prompts import system_prompt
from tests.support import HAPPY_POLICY, NOW


def state(prompt: str) -> dict:
    return json.loads(prompt.split("Current claim state:\n")[1])


def test_the_claim_state_leaves_out_values_that_do_not_apply():
    # Left over from answers the declarant changed: a broken glass has no other vehicle, and a
    # policyholder driving has no driver name.
    claim = Claim(
        loss_type=LossType.BROKEN_GLASS,
        other_vehicle_plate="1234BCD",
        driver_is_policyholder=True,
        driver_name="Pedro Ruiz",
    )

    collected = state(system_prompt(claim, HAPPY_POLICY, NOW))["collected"]

    assert collected == {"driver_is_policyholder": True, "loss_type": "broken_glass"}
