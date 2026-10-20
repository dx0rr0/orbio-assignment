import pytest

from claims_agent.domain import Claim, LossType
from claims_agent.domain.next_steps import next_steps

APPRAISAL = "Asignar perito para valorar los daños."


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        (Claim(loss_type=LossType.OTHER, injuries=False, vehicle_immobilized=False), [APPRAISAL]),
        (
            Claim(loss_type=LossType.BROKEN_GLASS, vehicle_immobilized=True),
            [
                "Ofrecer grúa y taller concertado.",
                "Ofrecer cita en un centro de reparación de lunas.",
                APPRAISAL,
            ],
        ),
        (
            Claim(loss_type=LossType.THEFT, police_report_filed=False),
            ["Pedir al declarante que presente denuncia ante la policía.", APPRAISAL],
        ),
        (
            Claim(loss_type=LossType.THEFT, police_report_filed=True),
            ["Solicitar copia de la denuncia.", APPRAISAL],
        ),
        (
            Claim(
                loss_type=LossType.COLLISION,
                other_vehicle_involved=True,
                friendly_report_signed=True,
            ),
            ["Solicitar copia del parte amistoso firmado.", APPRAISAL],
        ),
        (Claim(loss_type=LossType.COLLISION, other_vehicle_involved=False), [APPRAISAL]),
    ],
)
def test_next_steps_follow_the_claim(claim, expected):
    assert list(next_steps(claim)) == expected


def test_injuries_come_first():
    steps = next_steps(Claim(loss_type=LossType.COLLISION, injuries=True, vehicle_immobilized=True))
    assert steps[0] == "Llamar al declarante hoy para conocer el estado de los heridos."
