import pytest

from claims_agent.domain import (
    Claim,
    ClaimStatus,
    Conversation,
    IncompleteClaimError,
    Message,
    Role,
    Urgency,
)
from claims_agent.engine import BotMessage, Voice
from claims_agent.engine.summary import RECORD_KEY_POINTS, SummaryError, summarize
from claims_agent.engine.tools import CONFIRM_CLAIM, UPDATE_CLAIM
from claims_agent.engine.voice import speech_chunks
from claims_agent.ports import SpeechError
from tests.engine.fakes import COMPLETE, LLM_DOWN, NOW, call, say

# --- voice --------------------------------------------------------------------------------


class Speech:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.texts: list[str] = []

    async def transcribe(self, wav: bytes) -> str:
        if self.fail:
            raise SpeechError("down")
        return "  hola  "

    async def synthesize(self, text: str) -> bytes:
        if self.fail:
            raise SpeechError("down")
        self.texts.append(text)
        return b"wav"


async def test_transcription_is_stripped():
    assert await Voice(Speech(), Speech()).transcribe(b"x") == "hola"


async def test_failed_transcription_becomes_an_empty_utterance():
    assert await Voice(Speech(fail=True), Speech()).transcribe(b"x") == ""


async def test_a_part_of_an_utterance_that_fails_to_transcribe_is_none():
    assert await Voice(Speech(fail=True), Speech()).transcribe_part(b"x") is None


def test_the_parts_of_an_utterance_are_joined_skipping_silent_ones():
    assert Voice.join_parts(["Hola,", "", "quiero dar un parte"]) == "Hola, quiero dar un parte"


def test_an_utterance_with_a_failed_part_is_empty():
    assert Voice.join_parts(["Hola,", None]) == ""


async def test_all_replies_of_a_turn_are_spoken_once_using_their_speech_text():
    tts = Speech()
    replies = [BotMessage("Uno."), BotMessage("Póliza AUT-1", speech="Póliza A U T, 1")]

    assert await Voice(Speech(), tts).speak(replies) == b"wav"
    assert tts.texts == ["Uno. Póliza A U T, 1"]


async def test_failed_synthesis_gives_no_audio():
    assert await Voice(Speech(), Speech(fail=True)).speak([BotMessage("Hola")]) is None


# --- speech by sentence -------------------------------------------------------------------


def test_speech_is_cut_into_sentences_joining_short_pieces():
    text = "Le leo los datos de la declaración: Declarante: María López. ¿Es todo correcto?"

    assert speech_chunks(text) == [
        "Le leo los datos de la declaración:",
        "Declarante: María López. ¿Es todo correcto?",
    ]


def test_a_short_last_piece_joins_the_chunk_before_it():
    assert speech_chunks("Muy bien, gracias por esperar. Bien.") == [
        "Muy bien, gracias por esperar. Bien."
    ]
    assert speech_chunks("Hola.") == ["Hola."]
    assert speech_chunks("  ") == []


def test_times_and_spelled_identifiers_are_not_cut():
    assert speech_chunks("Fue a las 19:00, póliza A U T, 4 8 2 1.") == [
        "Fue a las 19:00, póliza A U T, 4 8 2 1."
    ]


async def test_replies_are_spoken_by_sentence_in_order():
    tts = Speech()
    replies = [
        BotMessage("Si hay alguien herido, llame al 112."),
        BotMessage("¿Dónde ocurrió el siniestro?"),
    ]

    chunks = [wav async for wav in Voice(Speech(), tts).speak_in_chunks(replies)]

    assert chunks == [b"wav", b"wav"]
    assert sorted(tts.texts) == sorted(
        ["Si hay alguien herido, llame al 112.", "¿Dónde ocurrió el siniestro?"]
    )


async def test_failed_synthesis_ends_the_spoken_chunks():
    chunks = [
        wav
        async for wav in Voice(Speech(), Speech(fail=True)).speak_in_chunks(
            [BotMessage("Hola, buenas tardes.")]
        )
    ]

    assert chunks == []


# --- turn result --------------------------------------------------------------------------


async def test_only_the_confirming_turn_reports_a_confirmed_claim(engine, llm, conversation_id):
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))

    completing = await engine.handle_text(conversation_id, "Todos los datos")
    confirming = await engine.handle_text(conversation_id, "Sí")
    after = await engine.handle_text(conversation_id, "¿Sigue ahí?")

    assert [completing.confirmed_claim, confirming.confirmed_claim, after.confirmed_claim] == [
        False,
        True,
        False,
    ]


# --- summary ------------------------------------------------------------------------------


def confirmed_conversation() -> Conversation:
    claim = Claim(
        status=ClaimStatus.CONFIRMED,
        loss_type="collision",
        injuries=True,
        vehicle_immobilized=False,
        other_vehicle_involved=True,
        friendly_report_signed=False,
    )
    return (
        Conversation.start(NOW)
        .model_copy(update={"claim": claim})
        .add(Message(role=Role.USER, content="Me golpearon por detrás en un semáforo"))
    )


async def test_summary_combines_llm_key_points_with_code_decisions(llm):
    llm.then(call(RECORD_KEY_POINTS, {"key_points": [" Golpe por alcance. ", "", "Un herido."]}))

    summary = await summarize(llm, confirmed_conversation())

    assert summary.key_points == ("Golpe por alcance.", "Un herido.")
    assert summary.urgency is Urgency.HIGH
    assert summary.next_steps == (
        "Llamar al declarante hoy para conocer el estado de los heridos.",
        "Contactar con el otro conductor o su aseguradora para el parte.",
        "Asignar perito para valorar los daños.",
    )
    assert "Declarante: Me golpearon por detrás" in llm.requests[0]["messages"][0].content


@pytest.mark.parametrize(
    "completion",
    [
        say("Resumen en texto libre"),
        call(RECORD_KEY_POINTS, "{not json"),
        call(RECORD_KEY_POINTS, {"key_points": []}),
        call(RECORD_KEY_POINTS, {"key_points": [1, 2]}),
        LLM_DOWN,
    ],
)
async def test_unusable_summaries_raise(llm, completion):
    llm.then(completion)

    with pytest.raises(SummaryError):
        await summarize(llm, confirmed_conversation())


async def test_a_claim_without_the_data_urgency_needs_is_not_summarized(llm):
    conversation = confirmed_conversation()
    undecided = conversation.claim.model_copy(update={"injuries": None})

    with pytest.raises(IncompleteClaimError, match="urgency"):
        await summarize(llm, conversation.model_copy(update={"claim": undecided}))

    assert llm.requests == []
