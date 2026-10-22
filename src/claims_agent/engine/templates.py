"""Messages written by code, never by the LLM: anything the declarant could act on or rely on
(the read-back, the reference, coverage decisions, the 112 advice) comes from here.

Each message has a display text and, when they differ, a speech text.
"""

from dataclasses import dataclass
from datetime import date, datetime, time

from claims_agent.domain import (
    MADRID,
    Claim,
    ClaimStatus,
    ClosureReason,
    CompleteClaim,
    LossLocation,
    LossType,
    LossWindow,
    Policy,
)

INSURER = "Seguros Brisa"

_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

LOSS_TYPE_NAMES: dict[LossType, str] = {
    LossType.COLLISION: "colisión",
    LossType.THEFT: "robo",
    LossType.BROKEN_GLASS: "rotura de lunas",
    LossType.PARKED_DAMAGE: "daños con el vehículo aparcado",
    LossType.OTHER: "otro tipo de siniestro",
}
# As the read-back says them: "Resumo su declaración: una colisión, …".
_LOSS_TYPE_SUMMARY: dict[LossType, str] = {
    LossType.COLLISION: "una colisión",
    LossType.THEFT: "un robo",
    LossType.BROKEN_GLASS: "una rotura de lunas",
    LossType.PARKED_DAMAGE: "daños con el vehículo aparcado",
    LossType.OTHER: "un siniestro",
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
        """What is said aloud. `Voice` spells its identifiers out for the TTS."""
        return self.speech or self.text


# --- formatting ---------------------------------------------------------------------------


def display_plate(plate: str) -> str:
    """Current Spanish plates ('1234BCD') are written '1234 BCD'; others are left as they are."""
    if len(plate) == 7 and plate[:4].isdigit() and plate[4:].isalpha():
        return f"{plate[:4]} {plate[4:]}"
    return plate


def _place(location: LossLocation) -> str:
    """'A-3, Arganda del Rey (Madrid)': the province only when it is not the municipality's
    name, so not 'Valencia (Valencia)'."""
    place = ", ".join(p for p in (location.way, location.municipality) if p)
    if location.province != location.municipality:
        place = f"{place} ({location.province})"
    return place


def _date(value: date) -> str:
    return f"{value.day} de {_MONTHS[value.month - 1]} de {value.year}"


def _time(value: datetime) -> str:
    return f"{value:%H:%M}"


def format_loss_window(window: LossWindow) -> str:
    start, end = window.start.astimezone(MADRID), window.end.astimezone(MADRID)
    # Whole days ("ayer"): no hours, which would read "entre las 00:00 y las 23:59".
    if start.time() == time.min and end.time() >= time(23, 59):
        if start.date() == end.date():
            return f"el {_date(start)}, sin hora concreta"
        return f"entre el {_date(start)} y el {_date(end)}, sin hora concreta"
    if start == end:
        return f"el {_date(start)} a las {_time(start)}"
    if start.date() == end.date():
        return f"el {_date(start)}, entre las {_time(start)} y las {_time(end)}"
    return f"entre el {_date(start)} a las {_time(start)} y el {_date(end)} a las {_time(end)}"


def _sentence(text: str) -> str:
    """Free text ends with punctuation, so the next read-back line doesn't run into it."""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _yes_no(*, value: bool | None) -> str:
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


def fixed_messages() -> tuple[BotMessage, ...]:
    """The messages whose text never changes, so their speech can be synthesized once."""
    return (greeting(), ask_to_repeat(), technical_problem(), emergency_advice())


def read_back(incomplete: Claim) -> BotMessage:
    """A short summary of the loss, for the declarant to confirm the complete claim: what,
    when and where, injuries and damage, the other vehicle or the police report. Raises
    `IncompleteClaimError` if anything is missing.

    About 15 seconds of speech. The policy number and the plate are left out because they
    were checked against the policy; names and the description because they are the
    declarant's own words. All of it is on screen."""
    claim = incomplete.complete()
    injuries = "Hubo heridos" if claim.injuries else "No hubo heridos"
    vehicle = "quedó inmovilizado" if claim.vehicle_immobilized else "puede circular"

    sentences = [
        (
            f"Resumo su declaración: {_LOSS_TYPE_SUMMARY[claim.loss_type]}, "
            f"{format_loss_window(claim.loss_window)}, en {_place(claim.loss_location)}."
        ),
        # A stolen vehicle is neither drivable nor immobilized.
        f"{injuries}."
        if claim.loss_type is LossType.THEFT
        else f"{injuries} y el vehículo {vehicle}.",
    ]
    if claim.loss_type is LossType.COLLISION:
        # Set exactly when another vehicle was involved.
        if (plate := claim.other_vehicle_plate) is not None:
            signed = "firmaron" if claim.friendly_report_signed else "no firmaron"
            sentences.append(
                f"Hubo otro vehículo implicado, matrícula {display_plate(plate)}, "
                f"y {signed} parte amistoso."
            )
        else:
            sentences.append("No hubo otro vehículo implicado.")
    if claim.loss_type is LossType.THEFT:
        filed = "Ya ha presentado" if claim.police_report_filed else "Aún no ha presentado"
        sentences.append(f"{filed} denuncia.")
    sentences.append("¿Es correcto?")
    return BotMessage(" ".join(sentences))


def _listed(intro: str, lines: list[str], outro: str) -> BotMessage:
    """A list on screen; spoken without its dashes, which the TTS would voice."""
    text = "\n".join([intro, *(f"- {line}" for line in lines), outro])
    return BotMessage(text, " ".join([intro, *lines, outro]))


def _field_lines(claim: CompleteClaim) -> list[tuple[tuple[str, ...], str]]:
    """One (fields, line) per value the declarant could correct, in the claim order."""
    driver = "el titular de la póliza" if claim.driver_is_policyholder else claim.driver_name
    lines: list[tuple[tuple[str, ...], str]] = [
        (("policy_number",), f"Póliza: {claim.policy_number}."),
        (("plate",), f"Matrícula: {display_plate(claim.plate)}."),
        (("declarant_name",), f"Declarante: {claim.declarant_name}."),
        (("driver_is_policyholder", "driver_name"), f"Conductor: {driver}."),
        (("loss_type",), f"Tipo de siniestro: {LOSS_TYPE_NAMES[claim.loss_type]}."),
        (("loss_window",), f"Fecha y hora: {format_loss_window(claim.loss_window)}."),
        (("loss_location",), f"Lugar: {_place(claim.loss_location)}."),
        (("description",), f"Descripción: {_sentence(claim.description)}"),
        (("injuries",), f"Heridos: {_yes_no(value=claim.injuries)}."),
        (
            ("vehicle_immobilized",),
            f"Vehículo inmovilizado: {_yes_no(value=claim.vehicle_immobilized)}.",
        ),
    ]
    if claim.loss_type is LossType.COLLISION:
        involved = ("other_vehicle_involved", "other_vehicle_plate")
        if (plate := claim.other_vehicle_plate) is not None:
            lines += [
                (involved, f"Otro vehículo implicado, matrícula {display_plate(plate)}."),
                (
                    ("friendly_report_signed",),
                    f"Parte amistoso firmado: {_yes_no(value=claim.friendly_report_signed)}.",
                ),
            ]
        else:
            lines.append((involved, "No hubo otro vehículo implicado."))
    if claim.loss_type is LossType.THEFT:
        lines.append(
            (
                ("police_report_filed",),
                f"Denuncia presentada: {_yes_no(value=claim.police_report_filed)}.",
            )
        )
    return lines


def read_back_changes(heard: Claim, incomplete: Claim) -> BotMessage:
    """After a correction, only the values that differ from the claim the declarant last heard
    read back, so the whole claim is not read again for one field. Raises
    `IncompleteClaimError` if anything is missing."""
    claim = incomplete.complete()
    changed = [
        line
        for fields, line in _field_lines(claim)
        if any(getattr(heard, field) != getattr(claim, field) for field in fields)
    ]
    question = "¿Es todo correcto?"
    if not changed:
        return BotMessage(f"No ha cambiado ningún dato. {question}")
    intro = "He corregido este dato:" if len(changed) == 1 else "He corregido estos datos:"
    return _listed(intro, changed, f"Lo demás queda igual. {question}")


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
    return BotMessage(
        f"Su declaración ha quedado registrada con la referencia {reference}. "
        "Un tramitador revisará el caso y se pondrá en contacto con usted. "
        "Gracias por su llamada."
    )


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
            return _conversation_closed()


def already_closed(claim: Claim) -> BotMessage:
    if claim.status is ClaimStatus.CONFIRMED and claim.reference:
        return BotMessage(
            f"Esta declaración ya está registrada con la referencia {claim.reference}. "
            "Si necesita declarar otro siniestro, inicie una nueva conversación."
        )
    return _conversation_closed()


def _conversation_closed() -> BotMessage:
    return BotMessage(
        "Esta conversación ya está cerrada. Si necesita declarar un siniestro, "
        "inicie una nueva conversación."
    )
