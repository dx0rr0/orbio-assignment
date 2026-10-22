import asyncio
import base64
import time
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.urls import path

from claims_agent.engine import templates
from claims_agent.engine.tools import CONFIRM_CLAIM, UPDATE_CLAIM
from claims_agent.ports import ConcurrentUpdateError, SpeechError
from claims_service.intake import consumers
from claims_service.intake.consumers import NOT_FOUND_CLOSE_CODE, ConversationConsumer
from claims_service.intake.groups import group_name
from claims_service.intake.routing import websocket_application
from tests.engine.fakes import COMPLETE, call, say
from tests.service.conftest import FAKE_PCM

pytestmark = pytest.mark.django_db(transaction=True)


async def receive_until(communicator, last_type: str) -> list[dict]:
    """Every event up to and including the first of `last_type`."""
    events = []
    while not events or events[-1]["type"] != last_type:
        events.append(await communicator.receive_json_from(timeout=5))
    return events


# Browsers send the page's origin; the frontend is served from an allowed host.
ORIGIN = (b"origin", b"http://localhost:8080")


async def start(application, path: str = "/ws/conversations/"):
    communicator = WebsocketCommunicator(application, path, headers=[ORIGIN])
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def started_conversation(application) -> tuple[WebsocketCommunicator, str]:
    communicator = await start(application)
    events = await receive_until(communicator, "audio_end")
    return communicator, events[0]["conversation_id"]


async def test_connecting_starts_a_conversation_with_a_spoken_greeting(application, tts):
    communicator = await start(application)

    events = await receive_until(communicator, "audio_end")

    assert [e["type"] for e in events] == [
        "conversation_started",
        "bot_message",
        "claim_updated",
        "audio",
        "audio_end",
    ]
    assert events[1]["text"] == templates.greeting().text
    assert events[2]["claim"]["status"] == "in_progress"
    assert events[2]["claim"]["fields"][0] == {
        "name": "policy_number",
        "state": "empty",
        "value": None,
        "error": None,
    }
    assert events[3]["mime"] == "audio/pcm;rate=24000"
    assert base64.b64decode(events[3]["data"]) == FAKE_PCM
    assert tts.spoken == [templates.greeting().text]
    await communicator.disconnect()


async def test_a_new_call_warms_up_the_provider_connections_at_once(application, warm_ups):
    communicator = await start(application)
    await receive_until(communicator, "audio_end")

    assert len(warm_ups) == 1
    await communicator.disconnect()


class QuickToCoolConsumer(ConversationConsumer):
    warm_up_every_s = 0.05
    keep_warm_s = 0.3


async def test_the_connections_are_kept_warm_while_the_call_is_active(services, warm_ups, llm):
    application = URLRouter(
        [path("ws/conversations/", QuickToCoolConsumer.as_asgi(services=services))]
    )
    communicator = await start(application)
    await receive_until(communicator, "audio_end")

    await asyncio.sleep(0.6)  # warmed every 0.05 s for 0.3 s, then nothing
    cooled = len(warm_ups)
    assert 4 <= cooled <= 8
    await asyncio.sleep(0.2)
    assert len(warm_ups) == cooled
    llm.then(say("¿Me indica su número de póliza?"))
    await communicator.send_json_to({"type": "text", "text": "Hola"})
    await receive_until(communicator, "claim_updated")
    await asyncio.sleep(0.1)

    assert len(warm_ups) > cooled  # any message from the declarant starts it again
    await communicator.disconnect()


async def test_text_turn_without_audio(application, llm, policies, tts):
    communicator, _ = await started_conversation(application)
    llm.then(
        call(UPDATE_CLAIM, {"policy_number": "AUT-48213907", "plate": "9999 ZZZ"}),
        say("La matrícula no coincide. ¿Puede repetirla?"),
    )

    await communicator.send_json_to({"type": "text", "text": "AUT 48213907, la 9999 ZZZ"})
    events = await receive_until(communicator, "claim_updated")

    assert events[0]["text"] == "La matrícula no coincide. ¿Puede repetirla?"
    fields = {f["name"]: f for f in events[1]["claim"]["fields"]}
    assert fields["policy_number"]["state"] == "valid"
    assert fields["plate"]["state"] == "invalid"
    assert fields["plate"]["error"] == "No coincide con la matrícula de la póliza AUT-48213907"
    assert len(tts.spoken) == 1  # only the greeting was spoken
    assert await communicator.receive_nothing()
    await communicator.disconnect()


async def test_audio_turn_sends_the_transcript_then_the_spoken_reply(application, llm, stt, tts):
    communicator, _ = await started_conversation(application)
    stt.transcripts.append("Hola, quiero dar un parte")
    llm.then(say("¿Me indica su número de póliza?"))
    wav = base64.b64encode(b"RIFF-utterance").decode()

    await communicator.send_json_to({"type": "audio", "data": wav})
    events = await receive_until(communicator, "audio")

    assert [e["type"] for e in events] == ["transcript", "bot_message", "claim_updated", "audio"]
    assert events[0]["text"] == "Hola, quiero dar un parte"
    assert tts.spoken[-1] == "¿Me indica su número de póliza?"
    await communicator.disconnect()


async def test_a_reply_is_spoken_by_sentence_as_each_one_is_ready(application, llm, tts):
    communicator, _ = await started_conversation(application)
    llm.then(say("Gracias por la información. ¿Me indica su número de póliza?"))

    await communicator.send_json_to({"type": "text", "text": "Hola", "reply_audio": True})
    events = await receive_until(communicator, "audio_end")

    assert [e["type"] for e in events] == [
        "bot_message",
        "claim_updated",
        "audio",
        "audio_continued",  # the pause between the sentences
        "audio_continued",
        "audio_end",
    ]
    assert tts.spoken[-2:] == ["Gracias por la información.", "¿Me indica su número de póliza?"]
    assert await communicator.receive_nothing()
    await communicator.disconnect()


async def test_speech_is_sent_in_pieces_as_it_arrives(application, llm, services, tts):
    communicator, _ = await started_conversation(application)
    piece = b"\x01\x00" * (services.voice.min_piece_bytes // 2)
    tts.pieces = [piece, piece, b"\x02\x00"]
    llm.then(say("¿Me indica su número de póliza?"))

    await communicator.send_json_to({"type": "text", "text": "Hola", "reply_audio": True})
    events = await receive_until(communicator, "claim_updated")
    audio = await receive_until(communicator, "audio_end")

    assert [e["type"] for e in audio] == [
        "audio",
        "audio_continued",
        "audio_continued",
        "audio_end",
    ]
    audio = audio[:-1]
    assert {e["mime"] for e in audio} == {"audio/pcm;rate=24000"}
    assert [base64.b64decode(e["data"]) for e in audio] == [piece, piece, b"\x02\x00"]
    assert events[0]["text"] == "¿Me indica su número de póliza?"
    assert await communicator.receive_nothing()
    await communicator.disconnect()


async def test_failed_transcription_asks_to_repeat(application, llm, stt):
    communicator, _ = await started_conversation(application)
    stt.transcripts.append(SpeechError("STT down"))

    await communicator.send_json_to({"type": "audio", "data": base64.b64encode(b"x").decode()})
    events = await receive_until(communicator, "audio")

    assert events[0] == {"type": "transcript", "text": ""}
    assert events[1]["text"] == templates.ask_to_repeat().text
    assert llm.requests == []
    await communicator.disconnect()


async def test_failed_synthesis_still_sends_the_text(application, llm, tts):
    communicator, _ = await started_conversation(application)
    tts.fail = True
    llm.then(say("¿Me indica su número de póliza?"))

    await communicator.send_json_to({"type": "text", "text": "Hola", "reply_audio": True})
    events = await receive_until(communicator, "claim_updated")

    assert events[0]["text"] == "¿Me indica su número de póliza?"
    # No audio, but the end of the turn's speech is still announced.
    assert await communicator.receive_json_from(timeout=5) == {"type": "audio_end"}
    assert await communicator.receive_nothing()
    await communicator.disconnect()


@pytest.mark.parametrize(
    ("message", "error"),
    [
        # The interface shows these to the declarant, so they are in Spanish.
        ([1, 2], "No se ha podido leer el mensaje"),
        ({"type": "video"}, "No se ha podido leer el mensaje"),
        ({"type": "text"}, "No se ha podido leer el mensaje"),
        ({"type": "text", "text": "a" * (consumers.MAX_TEXT_CHARS + 1)}, "demasiado largo"),
        ({"type": "audio", "data": "not base64!"}, "No se ha podido leer el audio"),
        (
            {"type": "audio", "data": "A" * (consumers.MAX_AUDIO_BASE64_CHARS + 1)},
            "demasiado largo",
        ),
    ],
)
async def test_malformed_messages_get_an_error_and_no_turn(application, llm, message, error):
    communicator, _ = await started_conversation(application)

    await communicator.send_json_to(message)
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "error"
    assert error in event["message"]
    assert llm.requests == []
    await communicator.disconnect()


async def test_confirmation_requests_the_summary(application, llm, policies, summary_requests):
    communicator, conversation_id = await started_conversation(application)
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))

    await communicator.send_json_to({"type": "text", "text": "Todos los datos"})
    await receive_until(communicator, "claim_updated")
    assert summary_requests == []
    await communicator.send_json_to({"type": "text", "text": "Sí, es correcto"})
    events = await receive_until(communicator, "claim_updated")

    assert events[-1]["claim"]["status"] == "confirmed"
    assert events[-1]["claim"]["reference"].startswith("SIN-")
    assert summary_requests == [conversation_id]
    await communicator.disconnect()


async def resume(application, conversation_id: str, message: object):
    communicator = WebsocketCommunicator(
        application, f"/ws/conversations/{conversation_id}/", headers=[ORIGIN]
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.send_json_to(message)
    return communicator


async def started_with_token(application) -> tuple[str, str]:
    first = await start(application)
    started = await first.receive_json_from(timeout=5)
    await receive_until(first, "audio")
    await first.disconnect()
    return started["conversation_id"], started["token"]


async def test_resuming_with_the_token_sends_a_snapshot(application, llm):
    conversation_id, token = await started_with_token(application)

    communicator = await resume(application, conversation_id, {"type": "resume", "token": token})
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "snapshot"
    assert event["conversation"]["id"] == conversation_id
    assert event["conversation"]["transcript"] == [
        {"role": "agent", "text": templates.greeting().text}
    ]
    await communicator.disconnect()


NOT_FOUND = {"type": "websocket.close", "code": NOT_FOUND_CLOSE_CODE}


async def test_unknown_conversation_is_closed_with_not_found(application):
    communicator = await resume(application, str(uuid4()), {"type": "resume", "token": "whatever"})

    assert await communicator.receive_output(timeout=5) == NOT_FOUND


@pytest.mark.parametrize(
    "message",
    [
        {"type": "resume"},
        {"type": "resume", "token": ""},
        {"type": "resume", "token": "not-the-token"},
        {"type": "resume", "token": 42},
        {"type": "text", "text": "Hola"},
        [1, 2],
    ],
)
async def test_resuming_without_the_token_is_closed_as_not_found(application, llm, message):
    # Not found, as for an unknown id: nobody learns which conversation ids exist.
    conversation_id, _ = await started_with_token(application)

    communicator = await resume(application, conversation_id, message)

    assert await communicator.receive_output(timeout=5) == NOT_FOUND
    assert llm.requests == []


class ImpatientConsumer(ConversationConsumer):
    resume_timeout_s = 0.1


def quick_to_give_up(services) -> URLRouter:
    consumer = ImpatientConsumer.as_asgi(services=services)
    return URLRouter([path("ws/conversations/<uuid:conversation_id>/", consumer)])


async def test_a_token_in_the_url_is_not_enough(llm, application, services):
    # URLs end up in logs, so the token only counts in the handshake message, sent in time.
    conversation_id, token = await started_with_token(application)
    communicator = WebsocketCommunicator(
        quick_to_give_up(services), f"/ws/conversations/{conversation_id}/?token={token}"
    )
    connected, _ = await communicator.connect()
    assert connected

    assert await communicator.receive_output(timeout=5) == NOT_FOUND
    await communicator.send_json_to({"type": "resume", "token": token})
    assert await communicator.receive_nothing()


async def test_background_updates_reach_watchers(application):
    communicator, conversation_id = await started_conversation(application)
    payload = {"type": "claim_updated", "claim": {"status": "incomplete"}, "summary": None}

    await get_channel_layer().group_send(
        group_name(UUID(conversation_id)), {"type": "claim_updated", "payload": payload}
    )

    assert await communicator.receive_json_from(timeout=5) == payload
    await communicator.disconnect()


async def test_a_broker_outage_does_not_break_the_confirmation(llm, policies, services):
    def broker_down(conversation_id: str) -> None:
        raise ConnectionError("redis is down")

    application = websocket_application(replace(services, request_summary=broker_down))
    communicator, _ = await started_conversation(application)
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))
    await communicator.send_json_to({"type": "text", "text": "Todos los datos"})
    await receive_until(communicator, "claim_updated")

    await communicator.send_json_to({"type": "text", "text": "Sí"})
    events = await receive_until(communicator, "claim_updated")

    assert events[-1]["claim"]["status"] == "confirmed"
    await communicator.send_json_to({"type": "text", "text": "Gracias"})
    assert (await communicator.receive_json_from(timeout=5))["type"] == "bot_message"
    await communicator.disconnect()


async def test_safety_advice_is_flagged_as_an_alert(application, llm):
    communicator, _ = await started_conversation(application)
    llm.then(call(UPDATE_CLAIM, {"injuries": True}), say("Lo siento. ¿Dónde ocurrió?"))

    await communicator.send_json_to({"type": "text", "text": "Mi hija está herida"})
    events = await receive_until(communicator, "claim_updated")

    assert [(e["text"][:30], e["alert"]) for e in events[:2]] == [
        (templates.emergency_advice().text[:30], True),
        ("Lo siento. ¿Dónde ocurrió?", False),
    ]
    await communicator.disconnect()


async def test_a_slow_broker_does_not_block_the_event_loop(llm, policies, services):
    def slow_broker(conversation_id: str) -> None:
        time.sleep(0.5)  # kombu retrying a connection, synchronously

    application = websocket_application(replace(services, request_summary=slow_broker))
    communicator, _ = await started_conversation(application)
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))
    await communicator.send_json_to({"type": "text", "text": "Todos los datos"})
    await receive_until(communicator, "claim_updated")

    gaps: list[float] = []

    async def tick() -> None:
        last = time.monotonic()
        for _ in range(20):
            await asyncio.sleep(0.02)
            gaps.append(time.monotonic() - last)
            last = time.monotonic()

    await communicator.send_json_to({"type": "text", "text": "Sí"})
    await asyncio.gather(tick(), receive_until(communicator, "claim_updated"))
    # Let the consumer finish queueing before the test tears the database down.
    await asyncio.sleep(0.6)

    assert max(gaps) < 0.3
    await communicator.disconnect()


class BrokenEngine:
    """Starts conversations with `engine` (none: fails to), then fails every turn."""

    def __init__(self, engine=None, error: Exception | None = None):
        self._engine = engine
        self._error = error or RuntimeError("database is down")

    async def start(self):
        if self._engine is None:
            raise self._error
        return await self._engine.start()

    async def handle_text(self, conversation_id, utterance):
        raise self._error


async def test_an_unexpected_error_in_a_turn_apologises_and_keeps_the_socket(engine, services):
    application = websocket_application(replace(services, engine=BrokenEngine(engine)))
    communicator, _ = await started_conversation(application)

    await communicator.send_json_to({"type": "text", "text": "Hola"})
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "bot_message"
    assert event["text"] == templates.technical_problem().text
    await communicator.send_json_to({"type": "text", "text": "¿Hola?"})
    assert (await communicator.receive_json_from(timeout=5))["type"] == "bot_message"
    await communicator.disconnect()


async def test_an_unexpected_error_when_connecting_apologises(services):
    application = websocket_application(replace(services, engine=BrokenEngine()))
    communicator = await start(application)

    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "bot_message"
    assert event["text"] == templates.technical_problem().text
    assert (await communicator.receive_output(timeout=5))["type"] == "websocket.close"


@pytest.mark.parametrize("headers", [[(b"origin", b"https://evil.example")], []])
async def test_connections_from_other_origins_are_rejected(application, headers):
    communicator = WebsocketCommunicator(application, "/ws/conversations/", headers=headers)

    connected, _ = await communicator.connect()

    assert not connected


async def test_a_concurrent_update_asks_to_repeat_in_spanish(engine, services):
    # Another turn of the same conversation (e.g. a second tab) saved first.
    racing = BrokenEngine(engine, error=ConcurrentUpdateError())
    application = websocket_application(replace(services, engine=racing))
    communicator, _ = await started_conversation(application)

    await communicator.send_json_to({"type": "text", "text": "Hola"})
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "bot_message"
    assert event["text"] == templates.technical_problem().text
    await communicator.disconnect()
