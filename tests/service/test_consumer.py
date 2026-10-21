import asyncio
import base64
import time
from uuid import UUID, uuid4

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from claims_agent.engine import templates
from claims_agent.engine.tools import CONFIRM_CLAIM, UPDATE_CLAIM
from claims_agent.ports import ConcurrentUpdateError, SpeechError
from claims_service.asgi import application
from claims_service.intake import consumers
from claims_service.intake.consumers import NOT_FOUND_CLOSE_CODE
from claims_service.intake.groups import group_name
from tests.engine.fakes import COMPLETE, call, say

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def summary_requests(monkeypatch) -> list[str]:
    requested: list[str] = []

    class FakeTask:
        @staticmethod
        def apply_async(args: list[str], **options: object) -> None:
            requested.extend(args)

    monkeypatch.setattr(consumers, "generate_claim_summary", FakeTask)
    return requested


async def receive_until(communicator, last_type: str) -> list[dict]:
    """Every event up to and including the first of `last_type`."""
    events = []
    while not events or events[-1]["type"] != last_type:
        events.append(await communicator.receive_json_from(timeout=5))
    return events


# Browsers send the page's origin; the frontend is served from an allowed host.
ORIGIN = (b"origin", b"http://localhost:8080")


async def start(path: str = "/ws/conversations/"):
    communicator = WebsocketCommunicator(application, path, headers=[ORIGIN])
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def started_conversation() -> tuple[WebsocketCommunicator, str]:
    communicator = await start()
    events = await receive_until(communicator, "audio")
    return communicator, events[0]["conversation_id"]


async def test_connecting_starts_a_conversation_with_a_spoken_greeting(tts):
    communicator = await start()

    events = await receive_until(communicator, "audio")

    assert [e["type"] for e in events] == [
        "conversation_started",
        "bot_message",
        "claim_updated",
        "audio",
    ]
    assert events[1]["text"] == templates.greeting().text
    assert events[2]["claim"]["status"] == "in_progress"
    assert events[2]["claim"]["fields"][0] == {
        "name": "policy_number",
        "state": "empty",
        "value": None,
        "error": None,
    }
    assert base64.b64decode(events[3]["data"]) == b"RIFF-fake-wav"
    assert tts.spoken == [templates.greeting().text]
    await communicator.disconnect()


async def test_text_turn_without_audio(llm, policies, tts):
    communicator, _ = await started_conversation()
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


async def test_audio_turn_sends_the_transcript_then_the_spoken_reply(llm, stt, tts):
    communicator, _ = await started_conversation()
    stt.transcripts.append("Hola, quiero dar un parte")
    llm.then(say("¿Me indica su número de póliza?"))
    wav = base64.b64encode(b"RIFF-utterance").decode()

    await communicator.send_json_to({"type": "audio", "data": wav})
    events = await receive_until(communicator, "audio")

    assert [e["type"] for e in events] == ["transcript", "bot_message", "claim_updated", "audio"]
    assert events[0]["text"] == "Hola, quiero dar un parte"
    assert tts.spoken[-1] == "¿Me indica su número de póliza?"
    await communicator.disconnect()


async def test_failed_transcription_asks_to_repeat(llm, stt):
    communicator, _ = await started_conversation()
    stt.transcripts.append(SpeechError("STT down"))

    await communicator.send_json_to({"type": "audio", "data": base64.b64encode(b"x").decode()})
    events = await receive_until(communicator, "audio")

    assert events[0] == {"type": "transcript", "text": ""}
    assert events[1]["text"] == templates.ask_to_repeat().text
    assert llm.requests == []
    await communicator.disconnect()


async def test_failed_synthesis_still_sends_the_text(llm, tts):
    communicator, _ = await started_conversation()
    tts.fail = True
    llm.then(say("¿Me indica su número de póliza?"))

    await communicator.send_json_to({"type": "text", "text": "Hola", "reply_audio": True})
    events = await receive_until(communicator, "claim_updated")

    assert events[0]["text"] == "¿Me indica su número de póliza?"
    assert await communicator.receive_nothing()  # no audio event
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
async def test_malformed_messages_get_an_error_and_no_turn(llm, message, error):
    communicator, _ = await started_conversation()

    await communicator.send_json_to(message)
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "error"
    assert error in event["message"]
    assert llm.requests == []
    await communicator.disconnect()


async def test_confirmation_requests_the_summary(llm, policies, summary_requests):
    communicator, conversation_id = await started_conversation()
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


async def test_reconnecting_with_the_token_sends_a_snapshot(llm):
    first = await start()
    started = await first.receive_json_from(timeout=5)
    await receive_until(first, "audio")
    await first.disconnect()
    conversation_id, token = started["conversation_id"], started["token"]

    communicator = await start(f"/ws/conversations/{conversation_id}/?token={token}")
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "snapshot"
    assert event["conversation"]["id"] == conversation_id
    assert event["conversation"]["transcript"] == [
        {"role": "agent", "text": templates.greeting().text}
    ]
    await communicator.disconnect()


async def test_unknown_conversation_is_closed_with_not_found():
    communicator = WebsocketCommunicator(
        application, f"/ws/conversations/{uuid4()}/?token=whatever", headers=[ORIGIN]
    )
    await communicator.connect()

    assert await communicator.receive_output(timeout=5) == {
        "type": "websocket.close",
        "code": NOT_FOUND_CLOSE_CODE,
    }


@pytest.mark.parametrize("query", ["", "?token=", "?token=not-the-token"])
async def test_resuming_without_the_token_is_closed_as_not_found(query):
    # Not found, as for an unknown id: nobody learns which conversation ids exist.
    first, conversation_id = await started_conversation()
    await first.disconnect()
    communicator = WebsocketCommunicator(
        application, f"/ws/conversations/{conversation_id}/{query}", headers=[ORIGIN]
    )
    await communicator.connect()

    assert await communicator.receive_output(timeout=5) == {
        "type": "websocket.close",
        "code": NOT_FOUND_CLOSE_CODE,
    }


async def test_background_updates_reach_watchers():
    communicator, conversation_id = await started_conversation()
    payload = {"type": "claim_updated", "claim": {"status": "incomplete"}, "summary": None}

    await get_channel_layer().group_send(
        group_name(UUID(conversation_id)), {"type": "claim_updated", "payload": payload}
    )

    assert await communicator.receive_json_from(timeout=5) == payload
    await communicator.disconnect()


async def test_a_broker_outage_does_not_break_the_confirmation(llm, policies, monkeypatch):
    class BrokenTask:
        @staticmethod
        def apply_async(args: list[str], **options: object) -> None:
            raise ConnectionError("redis is down")

    monkeypatch.setattr(consumers, "generate_claim_summary", BrokenTask)
    communicator, _ = await started_conversation()
    llm.then(call(UPDATE_CLAIM, COMPLETE), call(CONFIRM_CLAIM))
    await communicator.send_json_to({"type": "text", "text": "Todos los datos"})
    await receive_until(communicator, "claim_updated")

    await communicator.send_json_to({"type": "text", "text": "Sí"})
    events = await receive_until(communicator, "claim_updated")

    assert events[-1]["claim"]["status"] == "confirmed"
    await communicator.send_json_to({"type": "text", "text": "Gracias"})
    assert (await communicator.receive_json_from(timeout=5))["type"] == "bot_message"
    await communicator.disconnect()


async def test_safety_advice_is_flagged_as_an_alert(llm):
    communicator, _ = await started_conversation()
    llm.then(call(UPDATE_CLAIM, {"injuries": True}), say("Lo siento. ¿Dónde ocurrió?"))

    await communicator.send_json_to({"type": "text", "text": "Mi hija está herida"})
    events = await receive_until(communicator, "claim_updated")

    assert [(e["text"][:30], e["alert"]) for e in events[:2]] == [
        (templates.emergency_advice().text[:30], True),
        ("Lo siento. ¿Dónde ocurrió?", False),
    ]
    await communicator.disconnect()


async def test_a_slow_broker_does_not_block_the_event_loop(llm, policies, monkeypatch):
    class SlowTask:
        @staticmethod
        def apply_async(args: list[str], **options: object) -> None:
            time.sleep(0.5)  # kombu retrying a connection, synchronously

    monkeypatch.setattr(consumers, "generate_claim_summary", SlowTask)
    communicator, _ = await started_conversation()
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
    async def start(self):
        raise RuntimeError("database is down")

    async def handle_text(self, conversation_id, utterance):
        raise RuntimeError("database is down")


async def test_an_unexpected_error_in_a_turn_apologises_and_keeps_the_socket(monkeypatch):
    communicator, _ = await started_conversation()
    monkeypatch.setattr(consumers.services, "engine", BrokenEngine)

    await communicator.send_json_to({"type": "text", "text": "Hola"})
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "bot_message"
    assert event["text"] == templates.technical_problem().text
    await communicator.send_json_to({"type": "text", "text": "¿Hola?"})
    assert (await communicator.receive_json_from(timeout=5))["type"] == "bot_message"
    await communicator.disconnect()


async def test_an_unexpected_error_when_connecting_apologises(monkeypatch):
    monkeypatch.setattr(consumers.services, "engine", BrokenEngine)
    communicator = await start()

    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "bot_message"
    assert event["text"] == templates.technical_problem().text
    assert (await communicator.receive_output(timeout=5))["type"] == "websocket.close"


@pytest.mark.parametrize("headers", [[(b"origin", b"https://evil.example")], []])
async def test_connections_from_other_origins_are_rejected(headers):
    communicator = WebsocketCommunicator(application, "/ws/conversations/", headers=headers)

    connected, _ = await communicator.connect()

    assert not connected


async def test_a_concurrent_update_asks_to_repeat_in_spanish(monkeypatch):
    class RacingEngine:
        async def handle_text(self, conversation_id, utterance):
            raise ConcurrentUpdateError(conversation_id)

    communicator, _ = await started_conversation()
    monkeypatch.setattr(consumers.services, "engine", RacingEngine)

    await communicator.send_json_to({"type": "text", "text": "Hola"})
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "bot_message"
    assert event["text"] == templates.technical_problem().text
    await communicator.disconnect()
