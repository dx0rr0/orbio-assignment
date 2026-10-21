"""Spanish wording of validation errors for the live panel.

The domain's errors are written for the LLM, which explains them to the declarant in its own
words. The panel shows them to the declarant directly, so the ones about what the declarant
said are translated here; the rest (malformed tool arguments) are the agent's problem, not
the declarant's, and get a neutral message.
"""

import re

_TRANSLATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^there is no policy (?P<number>\S+)$"), "No existe la póliza {number}"),
    (
        re.compile(r"^does not match the plate on policy (?P<number>\S+)$"),
        "No coincide con la matrícula de la póliza {number}",
    ),
    (re.compile(r"^policy numbers look like"), "Las pólizas tienen el formato AUT-12345678"),
    (
        re.compile(r"^Spanish plates are"),
        "Las matrículas tienen 4 cifras y 3 consonantes, como 1234 BCD",
    ),
    (re.compile(r"^a plate must have"), "Una matrícula tiene entre 4 y 10 letras o cifras"),
    (
        re.compile(r"^'(?P<province>.+)' is not a Spanish province$"),
        "«{province}» no es una provincia española",
    ),
    (re.compile(r"^the loss cannot be in the future$"), "La fecha no puede ser futura"),
    (re.compile(r"^end cannot be earlier than start$"), "El final no puede ser anterior al inicio"),
)

UNTRANSLATED = "No se ha podido registrar; el asistente lo volverá a preguntar"

# Errors in what the client sent, which the interface shows as they are. Messages that are not
# the declarant's doing (malformed JSON from the client) still get a neutral Spanish wording.
UNREADABLE_MESSAGE = "No se ha podido leer el mensaje. Inténtelo de nuevo."
UNREADABLE_AUDIO = "No se ha podido leer el audio. Inténtelo de nuevo."
AUDIO_TOO_LONG = "El audio es demasiado largo: hable menos de un minuto seguido."
TEXT_TOO_LONG = "El mensaje es demasiado largo: escríbalo en varios más cortos."


def display_error(error: str) -> str:
    for pattern, spanish in _TRANSLATIONS:
        if match := pattern.search(error):
            return spanish.format(**match.groupdict())
    return UNTRANSLATED
