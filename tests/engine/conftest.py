import pytest

from claims_agent.adapters.memory import InMemoryConversationRepository
from claims_agent.engine import ConversationEngine, RetryingChatModel
from claims_agent.seed import seed_policies
from tests.engine.fakes import RecordingTelemetry, ScriptedLLM, no_sleep
from tests.support import NOW


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def telemetry() -> RecordingTelemetry:
    return RecordingTelemetry()


@pytest.fixture
def repository() -> InMemoryConversationRepository:
    return InMemoryConversationRepository(seed_policies(NOW.date()))


@pytest.fixture
def engine(llm, repository, telemetry) -> ConversationEngine:
    return ConversationEngine(
        llm=RetryingChatModel(llm, sleep=no_sleep),
        conversations=repository,
        policies=repository,
        references=repository,
        telemetry=telemetry,
        clock=lambda: NOW,
    )


@pytest.fixture
async def conversation_id(engine):
    return (await engine.start()).conversation.id
