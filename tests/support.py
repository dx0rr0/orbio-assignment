"""Data shared by the tests of every layer: the clock, the seeded policies and the claim of the
happy path."""

from datetime import datetime

from claims_agent.domain import MADRID
from claims_agent.seed import seed_policies

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=MADRID)
HAPPY_POLICY, OTHER_DRIVER_POLICY, EXPIRED_POLICY, NEW_POLICY, _ = seed_policies(NOW.date())


# Scripted update_claim payloads for the happy-path policy.
IDENTIFICATION = {"policy_number": "AUT-48213907", "plate": "4721 KLM"}
COMPLETE = IDENTIFICATION | {
    "declarant_name": "María López Fernández",
    "driver_is_policyholder": True,
    "loss_type": "broken_glass",
    "loss_window": {"start": "2026-10-19T19:00", "end": "2026-10-19T19:00"},
    "loss_location": {
        "way": "A-3",
        "municipality": "Arganda del Rey",
        "province": "Madrid",
        "country": "España",
    },
    "description": "Una piedra que saltó de un camión rompió el parabrisas",
    "injuries": False,
    "vehicle_immobilized": False,
}
