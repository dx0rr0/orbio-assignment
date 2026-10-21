"""Messages written by code, never by the LLM: anything the declarant could act on or rely on
(the read-back, the reference, coverage decisions, the 112 advice) comes from here.

Each message has a display text and a speech text: identifiers are spelled out for the TTS so
that "AUT-48213907" is not read as a large number.
"""

from dataclasses import dataclass
from datetime import date, datetime

from claims_agent.domain import (
    MADRID,
    Claim,
    ClaimStatus,
    ClosureReason,
    LossLocation,
    LossType,
    LossWindow,
    Policy,
)

INSURER = "Seguros Brisa"

_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip

LOSS_TYPE_NAMES: dict[LossType, str] = {
    LossType.COLLISION: "colisión",
    LossType.THEFT: "robo",
    LossType.BROKEN_GLASS: "rotura de lunas",
    LossType.PARKED_DAMAGE: "daños con el vehículo aparcado",
    LossType.OTHER: "otro tipo de siniestro",
}


@dataclass(frozen=True)
class BotMessage:
    text: str
    speech: str | None = None
    # False only for replies written by the LLM.
    scripted: bool = True
    # Safety advice the interface should make stand out.
    alert: bool = False

    @property
    def spoken(self) -> str:
        return self.speech or self.text


# --- formatting ---------------------------------------------------------------------------


def _spell(chars: str) -> str:
    return " ".join(chars)


def spoken_policy_number(number: str) -> str:
    """'AUT-48213907' → 'A U T, 4 8 2 1, 3 9 0 7'."""
    digits = number.removeprefix("AUT-")
    return f"A U T, {_spell(digits[:4])}, {_spell(digits[4:])}"


def display_plate(plate: str) -> str:
    """Current Spanish plates ('1234BCD') are written '1234 BCD'; others are left as they are."""
    if len(plate) == 7 and plate[:4].isdigit() and plate[4:].isalpha():
        return f"{plate[:4]} {plate[4:]}"
    return plate


def spoken_plate(plate: str) -> str:
    return ", ".join(_spell(part) for part in display_plate(plate).split())


def spoken_reference(reference: str) -> str:
    """'SIN-2026-000123' → 'S I N, 2026, 0 0 0 1 2 3'."""
    prefix, year, sequence = reference.split("-")
    return f"{_spell(prefix)}, {year}, {_spell(sequence)}"


def _date(value: date) -> str:
    return f"{value.day} de {_MONTHS[value.month - 1]} de {value.year}"


def _time(value: datetime) -> str:
    return f"{value:%H:%M}"


def format_loss_window(window: LossWindow) -> str:
    start, end = window.start.astimezone(MADRID), window.end.astimezone(MADRID)
    if start == end:
        return f"el {_date(start)} a las {_time(start)}"
    if start.date() == end.date():
        return f"el {_date(start)}, entre las {_time(start)} y las {_time(end)}"
    return f"entre el {_date(start)} a las {_time(start)} y el {_date(end)} a las {_time(end)}"


def _sentence(text: str) -> str:
    """Free text ends with punctuation, so the next read-back line doesn't run into it."""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _yes_no(value: bool | None) -> str:
    return "sí" if value else "no"


# --- conversation -------------------------------------------------------------------------


def greeting() -> BotMessage:
    return BotMessage(
        f"Hola, ha contactado con el servicio de siniestros de automóvil de {INSURER}. "
        "Soy un asistente virtual y le voy a ayudar a declarar el siniestro. "
        "Para empezar, ¿me indica su número de póliza y la matrícula del vehículo?"
    )


def ask_to_repeat() -> BotMessage:
    return BotMessage("Perdone, no le he entendido bien. ¿Puede repetirlo, por favor?")


def technical_problem() -> BotMessage:
    return BotMessage(
        "Disculpe, estoy teniendo un problema técnico. ¿Puede repetirme lo último que me ha dicho?"
    )


def emergency_advice() -> BotMessage:
    return BotMessage(
        "Si hay alguien herido y necesita atención, llame ahora al 112. "
        "Cuando pueda, seguimos con la declaración.",
        alert=True,
    )


def read_back(incomplete: Claim) -> BotMessage:
    """The complete claim, for the declarant to confirm. Raises `IncompleteClaimError` if
    anything is missing."""
    claim = incomplete.complete()
    location = claim.loss_location
    place = ", ".join(p for p in (location.way, location.municipality) if p)
    place = f"{place} ({location.province})"
    driver = "el titular de la póliza" if claim.driver_is_policyholder else claim.driver_name

    # (display, speech) pairs; speech is None when it reads the same as the display.
    lines: list[tuple[str, str | None]] = [
        (
            f"Póliza {claim.policy_number}, matrícula {display_plate(claim.plate)}.",
            f"Póliza {spoken_policy_number(claim.policy_number)}, "
            f"matrícula {spoken_plate(claim.plate)}.",
        ),
        (f"Declarante: {claim.declarant_name}. Conductor: {driver}.", None),
        (
            f"Siniestro: {LOSS_TYPE_NAMES[claim.loss_type]}, "
            f"{format_loss_window(claim.loss_window)}, en {place}.",
            None,
        ),
        (f"Descripción: {_sentence(claim.description)}", None),
        (
            f"Heridos: {_yes_no(claim.injuries)}. "
            f"Vehículo inmovilizado: {_yes_no(claim.vehicle_immobilized)}.",
            None,
        ),
    ]
    if claim.loss_type is LossType.COLLISION:
        # Set exactly when another vehicle was involved.
        if (plate := claim.other_vehicle_plate) is not None:
            report = f"Parte amistoso firmado: {_yes_no(claim.friendly_report_signed)}."
            lines.append(
                (
                    f"Otro vehículo implicado, matrícula {display_plate(plate)}. {report}",
                    f"Otro vehículo implicado, matrícula {spoken_plate(plate)}. {report}",
                )
            )
        else:
            lines.append(("No hubo otro vehículo implicado.", None))
    if claim.loss_type is LossType.THEFT:
        lines.append((f"Denuncia presentada: {_yes_no(claim.police_report_filed)}.", None))

    intro = "Le leo los datos de la declaración:"
    question = "¿Es todo correcto?"
    text = "\n".join([intro, *(f"- {display}" for display, _ in lines), question])
    speech = " ".join([intro, *(speech or display for display, speech in lines), question])
    return BotMessage(text, speech)


# --- closing ------------------------------------------------------------------------------


def confirm_coverage_value(claim: Claim, policy: Policy | None) -> BotMessage:
    """Asks the declarant to confirm the value that would leave the loss uncovered, before the
    claim closes over it. `policy` is the claim's identified policy. Raises `ValueError` if
    there is no such value, or the value or policy it comes from is missing."""
    match claim.coverage_issue, claim.loss_location, claim.loss_window, policy:
        case ClosureReason.OUTSIDE_SPAIN, LossLocation() as location, _, _:
            place = f"{location.municipality} ({location.country})"
            return BotMessage(
                f"Me ha indicado que el siniestro ocurrió en {place}. Por esta vía solo podemos "
                "tramitar siniestros ocurridos en España. ¿Es correcto el lugar? Si no lo es, "
                "dígame dónde ocurrió."
            )
        case (
            (ClosureReason.OUTSIDE_COVERAGE_PERIOD | ClosureReason.PARTIAL_COVERAGE) as issue,
            _,
            LossWindow() as window,
            Policy() as policy,
        ):
            outside = (
                "queda en parte fuera" if issue is ClosureReason.PARTIAL_COVERAGE else "queda fuera"
            )
            return BotMessage(
                f"Me ha indicado que el siniestro ocurrió {format_loss_window(window)}. "
                f"Su póliza está en vigor del {_date(policy.start_date)} al "
                f"{_date(policy.end_date)}, así que esa fecha {outside} de la vigencia. "
                "¿Es correcta? Si no lo es, dígame cuándo ocurrió."
            )
        case issue, _, _, _:
            raise ValueError(f"nothing to confirm for coverage issue {issue}")


def confirmed(reference: str) -> BotMessage:
    def message(ref: str) -> str:
        return (
            f"Su declaración ha quedado registrada con la referencia {ref}. "
            "Un tramitador revisará el caso y se pondrá en contacto con usted. "
            "Gracias por su llamada."
        )

    return BotMessage(message(reference), message(spoken_reference(reference)))


def closed(claim: Claim, policy: Policy | None) -> BotMessage:
    """Why the conversation ended, for every final status other than confirmed."""
    match claim.status, claim.closure_reason:
        case ClaimStatus.NOT_COVERED, ClosureReason.OUTSIDE_SPAIN:
            return BotMessage(
                "Lo siento, por esta vía solo podemos tramitar siniestros ocurridos en España, "
                "así que no puedo registrar esta declaración. Para siniestros en el extranjero, "
                "contacte con la asistencia en viaje de su póliza."
            )
        case ClaimStatus.NOT_COVERED, _:
            period = (
                f" (su vigencia va del {_date(policy.start_date)} al {_date(policy.end_date)})"
                if policy is not None
                else ""
            )
            return BotMessage(
                "Lo siento, según nuestros datos la póliza no estaba en vigor cuando ocurrió "
                f"el siniestro{period}, así que no puedo registrar esta declaración. "
                "Si cree que se trata de un error, contacte con su mediador."
            )
        case ClaimStatus.ESCALATED, ClosureReason.PARTIAL_COVERAGE:
            return BotMessage(
                "El siniestro cae en parte fuera del periodo de vigencia de la póliza, así que "
                "un tramitador revisará su caso personalmente y se pondrá en contacto con usted. "
                "Los datos que me ha dado quedan guardados."
            )
        case ClaimStatus.ESCALATED, ClosureReason.SYSTEM_ERROR:
            return BotMessage(
                "Lo siento, seguimos teniendo problemas técnicos. Un tramitador revisará su caso "
                "y se pondrá en contacto con usted. Los datos que me ha dado quedan guardados."
            )
        case ClaimStatus.ESCALATED, _:
            return BotMessage(
                "No he podido validar algunos de los datos, así que paso su caso a un tramitador, "
                "que se pondrá en contacto con usted para completarlo. "
                "Los datos que me ha dado quedan guardados."
            )
        case ClaimStatus.INCOMPLETE, ClosureReason.DATA_REFUSED:
            return BotMessage(
                "Entendido. Sin ese dato no podemos completar la declaración por esta vía. "
                "Guardamos lo que nos ha indicado; puede contactar de nuevo cuando quiera."
            )
        case _:
            return BotMessage(
                "Esta conversación ya está cerrada. Si necesita declarar un siniestro, "
                "inicie una nueva conversación."
            )


def already_closed(claim: Claim) -> BotMessage:
    if claim.status is ClaimStatus.CONFIRMED and claim.reference:
        return BotMessage(
            f"Esta declaración ya está registrada con la referencia {claim.reference}. "
            "Si necesita declarar otro siniestro, inicie una nueva conversación.",
            f"Esta declaración ya está registrada con la referencia "
            f"{spoken_reference(claim.reference)}. "
            "Si necesita declarar otro siniestro, inicie una nueva conversación.",
        )
    return BotMessage(
        "Esta conversación ya está cerrada. Si necesita declarar un siniestro, "
        "inicie una nueva conversación."
    )
