"""The production composition (`services`, the Celery worker hook and the ASGI application)
builds and closes. With a dummy key and Langfuse off: nothing here calls a provider."""

import importlib
from collections.abc import Sequence

import pytest
from asgiref.testing import ApplicationCommunicator

from claims_agent.adapters.openrouter import OpenRouterChatModel
from claims_agent.domain import Message
from claims_agent.engine import ConversationEngine
from claims_agent.ports import Completion, LLMError, ToolSpec
from claims_service.celery import configure_tasks
from claims_service.intake import services, tasks
from tests.engine.fakes import ScriptedLLM, say


@pytest.fixture(autouse=True)
def offline_settings(settings):
    settings.OPENROUTER_API_KEY = "sk-or-test-dummy"
    settings.LANGFUSE_ENABLED = False


async def test_the_consumer_services_build_and_close():
    consumer_services = services.for_consumer()

    assert isinstance(consumer_services.engine, ConversationEngine)
    await consumer_services.aclose()


class FailsOnce:
    """A provider client whose first completion fails, as a transient provider error does."""

    def __init__(self) -> None:
        self.llm = ScriptedLLM().then(LLMError("503"), say("¿Cuál es su número de póliza?"))

    async def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Completion:
        return await self.llm.complete(system=system, messages=messages, tools=tools)

    async def warm_up(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


@pytest.mark.django_db(transaction=True)
async def test_the_consumer_engine_retries_a_failed_llm_call(monkeypatch):
    monkeypatch.setattr(services, "chat_model_from_settings", FailsOnce)
    consumer_services = services.for_consumer()
    started = await consumer_services.engine.start()

    result = await consumer_services.engine.handle_text(started.conversation.id, "Hola")

    assert [reply.text for reply in result.replies] == ["¿Cuál es su número de póliza?"]
    await consumer_services.aclose()


async def test_the_task_services_open_and_close_a_chat_model():
    task_services = services.for_tasks()

    async with task_services.open_chat_model() as llm:
        assert isinstance(llm, OpenRouterChatModel)


@pytest.mark.django_db
def test_a_worker_process_configures_the_tasks(unconfigured_tasks):
    with pytest.raises(RuntimeError, match="no services"):
        tasks.queue_missing_summaries()

    configure_tasks()

    assert tasks.queue_missing_summaries() == 0


async def test_the_asgi_application_builds_and_closes_its_services_at_shutdown():
    # Only the shutdown: the startup would synthesize the greeting with the real provider.
    asgi = importlib.import_module("claims_service.asgi")
    communicator = ApplicationCommunicator(asgi.application, {"type": "lifespan"})

    await communicator.send_input({"type": "lifespan.shutdown"})

    assert await communicator.receive_output(timeout=5) == {"type": "lifespan.shutdown.complete"}
