"""Django service tests: real Postgres (from docker compose), in-memory channel layer, and
scripted fakes for the LLM and speech providers."""

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.utils import timezone

from claims_agent.engine import ConversationEngine, Voice
from claims_agent.ports import NullTelemetry, SpeechError
from claims_agent.seed import seed_policies
from claims_service.intake import services
from claims_service.intake.models import PolicyRecord
from claims_service.intake.repository import DjangoConversationRepository
from tests.engine.fakes import RecordingTelemetry, ScriptedLLM, no_sleep


class FakeSpeechToText:
    def __init__(self) -> None:
        self.transcripts: list[str | Exception] = []

    async def transcribe(self, wav: bytes) -> str:
        step = self.transcripts.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeTextToSpeech:
    def __init__(self) -> None:
        self.fail = False
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        if self.fail:
            raise SpeechError("TTS down")
        self.spoken.append(text)
        return b"RIFF-fake-wav"


@pytest.fixture(autouse=True)
def in_memory_channel_layer(settings):
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@pytest.fixture(autouse=True)
async def close_async_connections():
    """The async ORM runs queries in asgiref's worker thread; close its connections after each
    test, or the test database cannot be dropped at the end."""
    yield
    await sync_to_async(connections.close_all)()


@pytest.fixture
def policies(db):
    for policy in seed_policies(timezone.localdate()):
        PolicyRecord.objects.create(**policy.model_dump())


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def stt() -> FakeSpeechToText:
    return FakeSpeechToText()


@pytest.fixture
def tts() -> FakeTextToSpeech:
    return FakeTextToSpeech()


@pytest.fixture
def repository() -> DjangoConversationRepository:
    return DjangoConversationRepository()


@pytest.fixture(autouse=True)
def fake_providers(monkeypatch, llm, stt, tts, repository):
    engine = ConversationEngine(
        llm=llm, repository=repository, telemetry=RecordingTelemetry(), sleep=no_sleep
    )
    monkeypatch.setattr(services, "engine", lambda: engine)
    monkeypatch.setattr(services, "voice", lambda: Voice(stt, tts))
    monkeypatch.setattr(services, "repository", lambda: repository)
    monkeypatch.setattr(services, "telemetry", NullTelemetry)
