"""Wiring of the engine to its adapters. The ASGI application (`for_consumer`) and each Celery
worker process (`for_tasks`) build their dependencies here once and hand them in; tests and the
evals build their own, with fakes."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import openai
from django.conf import settings

from claims_agent.adapters.openrouter import (
    OpenRouterChatModel,
    OpenRouterSpeechToText,
    OpenRouterTextToSpeech,
)
from claims_agent.engine import ConversationEngine, Voice
from claims_agent.ports import ConversationRepository, NullTelemetry, Telemetry
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


def telemetry_from_settings() -> Telemetry:
    if not settings.LANGFUSE_ENABLED:
        return NullTelemetry()
    from langfuse import get_client

    from claims_agent.adapters.langfuse_telemetry import LangfuseTelemetry

    return LangfuseTelemetry(get_client())


def chat_model_from_settings() -> OpenRouterChatModel:
    """With Langfuse on, the OpenAI client is its wrapper, which records each completion as
    a generation (tokens and cost) under the current span."""
    client_class: type[openai.AsyncOpenAI] = openai.AsyncOpenAI
    if settings.LANGFUSE_ENABLED:
        from langfuse.openai import AsyncOpenAI

        client_class = AsyncOpenAI
    return OpenRouterChatModel.create(settings.OPENROUTER_API_KEY, client_class=client_class)


def voice_from_settings(telemetry: Telemetry) -> Voice:
    return Voice(
        OpenRouterSpeechToText(settings.OPENROUTER_API_KEY),
        OpenRouterTextToSpeech(settings.OPENROUTER_API_KEY),
        telemetry,
    )


def request_summary(conversation_id: str) -> None:
    tasks.generate_claim_summary.apply_async(
        args=[conversation_id], retry_policy=SUMMARY_RETRY_POLICY
    )


def for_consumer() -> ConsumerServices:
    repository = DjangoConversationRepository()
    telemetry = telemetry_from_settings()
    return ConsumerServices(
        engine=ConversationEngine(
            llm=chat_model_from_settings(), repository=repository, telemetry=telemetry
        ),
        voice=voice_from_settings(telemetry),
        repository=repository,
        telemetry=telemetry,
        access=access,
        request_summary=request_summary,
    )


def for_tasks() -> TaskServices:
    return TaskServices(
        repository=DjangoConversationRepository(),
        telemetry=telemetry_from_settings(),
        new_chat_model=chat_model_from_settings,
        notify=tasks.notify_watchers,
        queue_summary=tasks.generate_claim_summary.delay,
    )
