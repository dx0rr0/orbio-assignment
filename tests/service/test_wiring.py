"""The production composition (`services`, the Celery worker hook and the ASGI application)
builds and closes. With a dummy key and Langfuse off: nothing here calls a provider."""

import importlib

import pytest
from asgiref.testing import ApplicationCommunicator

from claims_agent.adapters.openrouter import OpenRouterChatModel
from claims_agent.engine import ConversationEngine
from claims_service.celery import configure_tasks
from claims_service.intake import services, tasks


@pytest.fixture(autouse=True)
def offline_settings(settings):
    settings.OPENROUTER_API_KEY = "sk-or-test-dummy"
    settings.LANGFUSE_ENABLED = False


async def test_the_consumer_services_build_and_close():
    consumer_services = services.for_consumer()

    assert isinstance(consumer_services.engine, ConversationEngine)
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
