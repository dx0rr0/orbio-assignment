"""What the adjuster should do next with a confirmed claim. Business rules, so code decides
them; the LLM only writes the key points of the summary."""

from claims_agent.domain.claim import Claim, LossType


def next_steps(claim: Claim) -> tuple[str, ...]:
    steps: list[str] = []
    if claim.injuries:
        steps.append("Llamar al declarante hoy para conocer el estado de los heridos.")
    if claim.vehicle_immobilized:
        steps.append("Ofrecer grúa y taller concertado.")
    match claim.loss_type:
        case LossType.COLLISION if claim.other_vehicle_involved:
            if claim.friendly_report_signed:
                steps.append("Solicitar copia del parte amistoso firmado.")
            else:
                steps.append("Contactar con el otro conductor o su aseguradora para el parte.")
        case LossType.THEFT:
            if claim.police_report_filed:
                steps.append("Solicitar copia de la denuncia.")
            else:
                steps.append("Pedir al declarante que presente denuncia ante la policía.")
        case LossType.BROKEN_GLASS:
            steps.append("Ofrecer cita en un centro de reparación de lunas.")
    steps.append("Asignar perito para valorar los daños.")
    return tuple(steps)
