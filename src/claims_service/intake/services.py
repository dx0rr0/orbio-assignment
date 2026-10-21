"""Wiring of the engine to its adapters. The ASGI application (`for_consumer`) and each Celery
worker process (`for_tasks`) build their dependencies here once and hand them in; tests and the
evals build their own, with fakes."""

import importlib
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from django.conf import settings

from claims_agent.adapters.openrouter import (
    OpenRouterChatModel,
    OpenRouterSpeechToText,
    OpenRouterTextToSpeech,
)
from claims_agent.engine import ConversationEngine, Voice
from claims_agent.ports import ChatModel, ConversationRepository, NullTelemetry, Telemetry
from claims_service.intake import access, tasks
from claims_service.intake.repository import DjangoConversationRepository
from claims_service.intake.tasks import TaskServices

# Give up queueing the summary after about a second instead of kombu's default retries.
SUMMARY_RETRY_POLICY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.5}


class ConversationAccess(Protocol):
    """Who may resume a conversation; `claims_service.intake.access` in production."""

    async def issue_token(self, conversation_id: UUID) -> str: ...

    async def is_authorized(self, conversation_id: UUID, token: str | None) -> bool: ...


@dataclass(frozen=True)
class ConsumerServices:
    """What the WebSocket consumer runs with."""

    engine: ConversationEngine
    voice: Voice
    repository: ConversationRepository
    telemetry: Telemetry
    access: ConversationAccess
    # Queues the summary of a confirmed claim. It publishes to the broker, so it blocks.
    request_summary: Callable[[str], None]
    # What the adapters hold open (the speech clients), closed by `aclose`.
    resources: AsyncExitStack = field(default_factory=AsyncExitStack)

    async def aclose(self) -> None:
        """At shutdown (the ASGI lifespan's, see `routing.lifespan_application`)."""
        await self.resources.aclose()


def telemetry_from_settings() -> Telemetry:
    if not settings.LANGFUSE_ENABLED:
        return NullTelemetry()
    from langfuse import get_client

    from claims_agent.adapters.langfuse_telemetry import LangfuseTelemetry

    return LangfuseTelemetry(get_client())


def chat_model_from_settings() -> OpenRouterChatModel:
    """With Langfuse on, the OpenAI client is its wrapper, which records each completion as
    a generation (tokens and cost) under the current span."""
    if settings.LANGFUSE_ENABLED:
        # Importing it wraps the OpenAI client classes in place (`langfuse.openai.AsyncOpenAI`
        # is `openai.AsyncOpenAI`).
        importlib.import_module("langfuse.openai")
    return OpenRouterChatModel.create(settings.OPENROUTER_API_KEY)


@asynccontextmanager
async def open_chat_model() -> AsyncIterator[ChatModel]:
    """A chat model for one task: each task runs in its own event loop, and a client is bound
    to the loop it first ran on, so it is closed when the task is done with it."""
    llm = chat_model_from_settings()
    try:
        yield llm
    finally:
        await llm.aclose()


def voice_from_settings(telemetry: Telemetry, resources: AsyncExitStack) -> Voice:
    """One pooled client per adapter for the whole process, closed with `resources`."""
    stt = OpenRouterSpeechToText(settings.OPENROUTER_API_KEY)
    resources.push_async_callback(stt.aclose)
    tts = OpenRouterTextToSpeech(settings.OPENROUTER_API_KEY)
    resources.push_async_callback(tts.aclose)
    return Voice(stt, tts, telemetry)


def request_summary(conversation_id: str) -> None:
    tasks.generate_claim_summary.apply_async(
        args=[conversation_id], retry_policy=SUMMARY_RETRY_POLICY
    )


def for_consumer() -> ConsumerServices:
    repository = DjangoConversationRepository()
    telemetry = telemetry_from_settings()
    resources = AsyncExitStack()
    llm = chat_model_from_settings()
    resources.push_async_callback(llm.aclose)
    return ConsumerServices(
        engine=ConversationEngine(llm=llm, repository=repository, telemetry=telemetry),
        voice=voice_from_settings(telemetry, resources),
        repository=repository,
        telemetry=telemetry,
        access=access,
        request_summary=request_summary,
        resources=resources,
    )


def for_tasks() -> TaskServices:
    return TaskServices(
        repository=DjangoConversationRepository(),
        telemetry=telemetry_from_settings(),
        open_chat_model=open_chat_model,
        notify=tasks.notify_watchers,
        queue_summary=tasks.generate_claim_summary.delay,
    )
