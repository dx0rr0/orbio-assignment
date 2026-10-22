import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import pytest

from claims_agent.domain import (
    Claim,
    Conversation,
    IncompleteClaimError,
    Message,
    Role,
    Urgency,
    apply_update,
    confirm,
)
from claims_agent.engine import BotMessage, Voice, templates
from claims_agent.engine.summary import RECORD_KEY_POINTS, SummaryError, summarize
from claims_agent.engine.tools import CONFIRM_CLAIM, UPDATE_CLAIM
from claims_agent.engine.voice import SAMPLE_BYTES, speech_chunks, whole_samples
from claims_agent.ports import NullTelemetry, Span, SpeechError
from tests.engine.fakes import LLM_DOWN, call, say
from tests.support import COMPLETE, HAPPY_POLICY, NOW

# --- voice --------------------------------------------------------------------------------


class Speech:
    """Speech-to-text and text-to-speech fakes. A text's speech is yielded as the pieces in
    `pieces` (one 2-byte piece per text by default), then `fail` raises if set."""

    sample_rate = 24_000

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.texts: list[str] = []
        self.pieces: dict[str, list[bytes]] = {}

    async def transcribe(self, wav: bytes) -> str:
        if self.fail:
            raise SpeechError("down")
        return "  hola  "

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        self.texts.append(text)
        for piece in self.pieces.get(text, [b"pc"]):
            yield piece
        if self.fail:
            raise SpeechError("down")


async def spoken(audio: AsyncIterator[bytes]) -> list[bytes]:
    return [piece async for piece in audio]


async def test_transcription_is_stripped():
    assert await Voice(Speech(), Speech()).transcribe(b"x") == "hola"


async def test_failed_transcription_is_none():
    # Not "": the engine tells an STT outage from an utterance that could not be understood.
    assert await Voice(Speech(fail=True), Speech()).transcribe(b"x") is None


async def test_all_replies_of_a_turn_are_spoken_at_once_using_their_speech_text():
    tts = Speech()
    replies = [BotMessage("Uno."), BotMessage("Póliza AUT-1", speech="Póliza A U T, 1")]

    assert await spoken(Voice(Speech(), tts).speak(replies)) == [b"pc"]
    assert tts.texts == ["Uno. Póliza A U T, 1"]


async def test_identifiers_are_spelled_out_in_what_is_synthesized():
    tts = Speech()
    reply = BotMessage("Su póliza es AUT-48213907 y su matrícula 4721 KLM.", scripted=False)

    await spoken(Voice(Speech(), tts).speak_in_chunks([reply]))

    assert tts.texts == ["Su póliza es A, U, T, 48, 21, 39, 07 y su matrícula 47, 21, K, L, M."]


async def test_failed_synthesis_gives_no_audio():
    assert await spoken(Voice(Speech(), Speech(fail=True)).speak([BotMessage("Hola")])) == []
    assert await spoken(Voice(Speech(), Speech()).speak([])) == []


async def test_speech_is_sent_in_pieces_of_whole_samples_of_a_minimum_length():
    tts = Speech()
    voice = Voice(Speech(), tts)
    least = voice.min_piece_bytes
    # Pieces as the provider sends them: any size, cut in the middle of a sample.
    tts.pieces["Hola."] = [b"a" * 3, b"b" * (least - 4), b"c" * 5, b"d" * 3]

    pieces = await spoken(voice.speak([BotMessage("Hola.")]))

    assert [len(piece) for piece in pieces] == [least + 4, 2]
    assert b"".join(pieces) == (b"a" * 3 + b"b" * (least - 4) + b"c" * 5 + b"d" * 3)[:-1]


async def test_pieces_are_cut_at_a_whole_sample_and_a_half_sample_at_the_end_is_dropped():
    async def provider() -> AsyncIterator[bytes]:
        for chunk in (b"abc", b"de"):
            yield chunk

    assert await spoken(whole_samples(provider(), min_bytes=2)) == [b"ab", b"cd"]


async def test_a_piece_is_sent_before_its_sentence_is_fully_synthesized():
    more = asyncio.Event()

    class Slow(Speech):
        async def stream(self, text: str) -> AsyncIterator[bytes]:
            yield b"x" * 100_000
            await more.wait()
            yield b"y" * 2

    audio = Voice(Speech(), Slow()).speak([BotMessage("Hola.")])
    first = await asyncio.wait_for(anext(audio), timeout=1)
    more.set()

    assert first == b"x" * 100_000
    assert await spoken(audio) == [b"y" * 2]


# --- fixed messages -----------------------------------------------------------------------


async def test_the_greeting_is_synthesized_once_and_then_replayed():
    tts = Speech()
    tts.pieces[templates.greeting().spoken] = [b"saludo" * 2_000, b"fin"]
    voice = Voice(Speech(), tts)

    first = await spoken(voice.speak([templates.greeting()]))
    again = await spoken(voice.speak([templates.greeting()]))

    assert again == first
    assert b"".join(first).startswith(b"saludo")
    assert tts.texts == [templates.greeting().spoken]


async def test_fixed_messages_spoken_by_sentence_are_kept_too():
    tts = Speech()
    voice = Voice(Speech(), tts)

    for _ in range(2):
        await spoken(voice.speak_in_chunks([templates.emergency_advice()]))

    assert tts.texts == speech_chunks(templates.emergency_advice().spoken)


async def test_replies_written_by_the_llm_are_synthesized_every_time():
    tts = Speech()
    voice = Voice(Speech(), tts)
    reply = BotMessage("Gracias, María. ¿Conducía usted el vehículo?", scripted=False)

    for _ in range(2):
        await spoken(voice.speak_in_chunks([reply]))

    assert tts.texts == [reply.spoken, reply.spoken]


async def test_a_failed_synthesis_is_not_kept():
    tts = Speech(fail=True)
    voice = Voice(Speech(), tts)
    await spoken(voice.speak([templates.greeting()]))
    tts.fail = False

    assert await spoken(voice.speak([templates.greeting()])) == [b"pc"]
    assert len(tts.texts) == 2


# --- speech by sentence -------------------------------------------------------------------


def test_speech_is_cut_into_sentences_joining_short_pieces():
    text = "Le leo los datos: Declarante: María López. Heridos: no. Vehículo inmovilizado: no."

    assert speech_chunks(text) == [
        "Le leo los datos: Declarante: María López.",
        "Heridos: no. Vehículo inmovilizado: no.",
    ]


def test_a_short_last_piece_joins_the_chunk_before_it():
    assert speech_chunks("Muy bien, gracias por esperar. Bien.") == [
        "Muy bien, gracias por esperar. Bien."
    ]
    assert speech_chunks("Hola.") == ["Hola."]
    assert speech_chunks("  ") == []


def test_times_and_spelled_identifiers_are_not_cut():
    assert speech_chunks("Fue a las 19:00, póliza A, U, T, 48, 21.") == [
        "Fue a las 19:00, póliza A, U, T, 48, 21."
    ]


FIRST = "Si hay alguien herido, llame al 112."
SECOND = "¿Dónde ocurrió el siniestro?"


async def test_sentences_are_synthesized_at_once_and_spoken_in_order():
    second_done = asyncio.Event()

    class SecondFirst(Speech):
        """The second sentence ends first; the first waits for it."""

        async def stream(self, text: str) -> AsyncIterator[bytes]:
            if text == FIRST:
                yield b"1a"
                await second_done.wait()
                yield b"1b"
            else:
                yield b"2a"
                second_done.set()

    voice = Voice(Speech(), SecondFirst())
    audio = voice.speak_in_chunks([BotMessage(FIRST), BotMessage(SECOND)])

    # Pieces shorter than the minimum are sent together at the end of their sentence, and a
    # pause of silence goes between sentences.
    pause = voice.sentence_pause
    assert await asyncio.wait_for(spoken(audio), timeout=1) == [b"1a1b", pause, b"2a"]
    # Silence, in whole samples.
    assert pause
    assert pause == bytes(len(pause))
    assert len(pause) % SAMPLE_BYTES == 0


async def test_failed_synthesis_ends_the_speech_there():
    tts = Speech(fail=True)
    voice = Voice(Speech(), tts)
    tts.pieces[FIRST] = [b"1" * voice.min_piece_bytes, b"lost"]

    pieces = await spoken(voice.speak_in_chunks([BotMessage(FIRST), BotMessage(SECOND)]))

    assert pieces == [b"1" * voice.min_piece_bytes]


class SpanUpdates(NullTelemetry):
    """Records what each span is updated with (the latency eval reads TTS errors there)."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, object, object]] = []

    @contextmanager
    def span(self, name: str, *, input: object = None) -> Iterator[Span]:
        updates = self.updates

        class Recorded:
            def update(self, *, output: object = None, metadata: object = None) -> None:
                updates.append((name, output, metadata))

        yield Recorded()


async def test_synthesis_is_recorded_in_its_span_failures_too():
    telemetry = SpanUpdates()
    tts = Speech()
    tts.pieces["Hola."] = [b"ab", b"cd"]

    await spoken(Voice(Speech(), tts, telemetry).speak([BotMessage("Hola.")]))
    tts.fail = True
    await spoken(Voice(Speech(), tts, telemetry).speak([BotMessage("Hola.")]))

    assert telemetry.updates == [
        ("tts", {"pcm_bytes": 4}, None),
        ("tts", None, {"error": "down"}),
    ]


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
    """A collision with an injured person and another vehicle, without a friendly report."""
    collision = COMPLETE | {
        "loss_type": "collision",
        "injuries": True,
        "other_vehicle_involved": True,
        "other_vehicle_plate": "1234 BCD",
        "friendly_report_signed": False,
    }
    claim = apply_update(Claim(), collision, policy=HAPPY_POLICY, now=NOW).claim
    return (
        Conversation.start(NOW)
        .model_copy(update={"claim": confirm(claim, "SIN-2026-000001")})
        .add(
            Message(role=Role.ASSISTANT, content="¿Qué ha ocurrido?"),
            Message(role=Role.USER, content="Me golpearon por detrás en un semáforo"),
        )
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


async def test_the_llm_is_given_the_claim_data_and_the_transcript_and_only_the_summary_tool(llm):
    conversation = confirmed_conversation()
    # A value that no longer applies to a collision, which the adjuster must not be told.
    stale = conversation.claim.model_copy(update={"police_report_filed": True})
    llm.then(call(RECORD_KEY_POINTS, {"key_points": ["Golpe por alcance."]}))

    await summarize(llm, conversation.model_copy(update={"claim": stale}))

    [request] = llm.requests
    assert RECORD_KEY_POINTS in request["system"]
    assert [tool["function"]["name"] for tool in request["tools"]] == [RECORD_KEY_POINTS]
    [message] = request["messages"]
    data, transcript = message.content.removeprefix("Claim data:\n").split("\n\nConversation:\n")
    assert json.loads(data).keys() == set(stale.required_fields)
    assert transcript == (
        "Agente: ¿Qué ha ocurrido?\nDeclarante: Me golpearon por detrás en un semáforo"
    )


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


async def test_an_incomplete_claim_is_not_summarized(llm):
    conversation = confirmed_conversation()
    undecided = conversation.claim.model_copy(update={"injuries": None})

    with pytest.raises(IncompleteClaimError, match="missing injuries"):
        await summarize(llm, conversation.model_copy(update={"claim": undecided}))

    assert llm.requests == []
