"""Django service tests: real Postgres (from docker compose), in-memory channel layer, and
scripted fakes for the LLM and speech providers."""

from collections.abc import AsyncIterator

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.utils import timezone

from claims_agent.engine import ConversationEngine, Voice
from claims_agent.ports import NullTelemetry, SpeechError
from claims_agent.seed import seed_policies
from claims_service.intake import access
from claims_service.intake.models import PolicyRecord
from claims_service.intake.repository import DjangoConversationRepository
from claims_service.intake.routing import websocket_application
from claims_service.intake.services import ConsumerServices
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
    """Each text is spoken as `FAKE_PCM`, in one piece unless `pieces` says otherwise."""

    sample_rate = 24_000

    def __init__(self) -> None:
        self.fail = False
        self.spoken: list[str] = []
        self.pieces: list[bytes] = [FAKE_PCM]

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        if self.fail:
            raise SpeechError("TTS down")
        self.spoken.append(text)
        for piece in self.pieces:
            yield piece


FAKE_PCM = b"pcm-fake"


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


@pytest.fixture
def engine(llm, repository) -> ConversationEngine:
    return ConversationEngine(
        llm=llm, repository=repository, telemetry=RecordingTelemetry(), sleep=no_sleep
    )


@pytest.fixture
def summary_requests() -> list[str]:
    return []


@pytest.fixture
def warm_ups() -> list[None]:
    """One entry per time the provider connections were warmed up."""
    return []


@pytest.fixture
def services(engine, stt, tts, repository, summary_requests, warm_ups) -> ConsumerServices:
    async def warm_up_connections() -> None:
        warm_ups.append(None)

    return ConsumerServices(
        engine=engine,
        voice=Voice(stt, tts),
        repository=repository,
        telemetry=NullTelemetry(),
        access=access,
        request_summary=summary_requests.append,
        warm_up_connections=warm_up_connections,
    )


@pytest.fixture
def application(services):
    """The WebSocket routes of the ASGI application, with the fakes above."""
    return websocket_application(services)
