import base64
from uuid import UUID, uuid4

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator

from claims_agent.engine import templates
from claims_agent.engine.tools import CONFIRM_CLAIM, UPDATE_CLAIM
from claims_agent.ports import SpeechError
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
        def delay(conversation_id: str) -> None:
            requested.append(conversation_id)

    monkeypatch.setattr(consumers, "generate_claim_summary", FakeTask)
    return requested


async def receive_until(communicator, last_type: str) -> list[dict]:
    """Every event up to and including the first of `last_type`."""
    events = []
    while not events or events[-1]["type"] != last_type:
        events.append(await communicator.receive_json_from(timeout=5))
    return events


async def start(path: str = "/ws/conversations/"):
    communicator = WebsocketCommunicator(application, path)
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
    assert "does not match" in fields["plate"]["error"]
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
        ({"type": "video"}, "expected"),
        ({"type": "text"}, "expected"),
        ({"type": "audio", "data": "not base64!"}, "base64"),
        ({"type": "audio", "data": "A" * (consumers.MAX_AUDIO_BASE64_CHARS + 1)}, "too long"),
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


async def test_reconnecting_sends_a_snapshot(llm):
    first, conversation_id = await started_conversation()
    await first.disconnect()

    communicator = await start(f"/ws/conversations/{conversation_id}/")
    event = await communicator.receive_json_from(timeout=5)

    assert event["type"] == "snapshot"
    assert event["conversation"]["id"] == conversation_id
    assert event["conversation"]["transcript"] == [
        {"role": "agent", "text": templates.greeting().text}
    ]
    await communicator.disconnect()


async def test_unknown_conversation_is_closed_with_not_found():
    communicator = WebsocketCommunicator(application, f"/ws/conversations/{uuid4()}/")
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
