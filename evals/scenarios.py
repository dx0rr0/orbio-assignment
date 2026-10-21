"""Eval scenarios: a persona for the simulated declarant and what the final claim must be."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claims_agent.domain import ClaimStatus, ClosureReason, LossType


@dataclass(frozen=True)
class Scenario:
    name: str
    persona: str
    status: ClaimStatus
    closure_reason: ClosureReason | None = None
    # Claim attributes and their expected values (callables check the value instead).
    expect: dict[str, Any] = field(default_factory=dict)
    max_turns: int = 14


def _starts_with(prefix: str) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        return isinstance(value, str) and value.startswith(prefix)

    check.__name__ = f"starts with {prefix!r}"
    return check


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="happy_path_broken_glass",
        persona=(
            "Eres María López Fernández, titular de la póliza AUT-48213907, coche con "
            "matrícula 4721 KLM. Ayer hacia las siete de la tarde, conduciendo tú por la A-3 a "
            "la altura de Arganda del Rey (Madrid), una piedra que saltó de un camión te rompió "
            "el parabrisas. Nadie resultó herido y el coche se puede conducir."
        ),
        status=ClaimStatus.CONFIRMED,
        expect={
            "policy_number": "AUT-48213907",
            "loss_type": LossType.BROKEN_GLASS,
            "driver_is_policyholder": True,
            "injuries": False,
            "vehicle_immobilized": False,
        },
    ),
    Scenario(
        name="collision_other_driver_injured",
        persona=(
            "Eres Javier Ruiz Martín, titular de la póliza AUT-51730284, matrícula 8305 HJT. "
            "Esta mañana a las nueve y cuarto tu hija Laura Ruiz conducía tu coche por la calle "
            "Colón de Valencia y otro coche, matrícula 2548 MNP, se saltó un ceda el paso y le "
            "dio en el lateral. Laura tiene dolor de cuello. El coche no arranca. Firmasteis el "
            "parte amistoso. Estás algo nervioso."
        ),
        status=ClaimStatus.CONFIRMED,
        expect={
            "loss_type": LossType.COLLISION,
            "driver_is_policyholder": False,
            "driver_name": _starts_with("Laura"),
            "injuries": True,
            "vehicle_immobilized": True,
            "other_vehicle_plate": "2548MNP",
            "friendly_report_signed": True,
            "urgency": "high",
        },
    ),
    Scenario(
        name="expired_policy",
        persona=(
            "Eres Lucía Navarro Gil, titular de la póliza AUT-62947105, matrícula 2196 FDS. "
            "Anteayer por la mañana, aparcada en la calle Mayor de Zaragoza, alguien te rayó "
            "todo el lateral del coche. Lo descubriste a las diez de la mañana; lo habías dejado "
            "allí la noche anterior a las nueve. Nadie herido, el coche funciona."
        ),
        status=ClaimStatus.NOT_COVERED,
        closure_reason=ClosureReason.OUTSIDE_COVERAGE_PERIOD,
    ),
    Scenario(
        name="theft_before_the_policy_started",
        persona=(
            "Eres Carlos Moreno Díaz, titular de la póliza AUT-73058416, matrícula 6614 LPR. "
            "Dejaste el coche aparcado el martes de la semana pasada por la noche en la calle "
            "Mayor de Alcalá de Henares y a la mañana siguiente ya no estaba. Pusiste denuncia."
        ),
        status=ClaimStatus.NOT_COVERED,
        closure_reason=ClosureReason.OUTSIDE_COVERAGE_PERIOD,
        expect={"loss_type": LossType.THEFT},
    ),
    Scenario(
        name="plate_mismatch_insisted",
        persona=(
            "Eres Elena Sánchez Ortiz, póliza AUT-84169527. Estás convencida de que la matrícula "
            "de tu coche es 1234 BCD y, aunque te digan que no coincide, repites siempre esa "
            "misma matrícula. Ayer te dieron un golpe por detrás en un semáforo de Sevilla."
        ),
        status=ClaimStatus.ESCALATED,
        closure_reason=ClosureReason.RETRIES_EXHAUSTED,
        expect={"plate": None},
    ),
    Scenario(
        name="refuses_to_give_name",
        persona=(
            "Llamas por la póliza AUT-48213907, matrícula 4721 KLM. Te niegas en redondo a dar "
            "tu nombre: no te fías y no quieres decirlo bajo ningún concepto, aunque te "
            "expliquen por qué hace falta. Si insisten, vuelves a negarte."
        ),
        status=ClaimStatus.INCOMPLETE,
        closure_reason=ClosureReason.DATA_REFUSED,
    ),
)
