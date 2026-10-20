"""Wiring of the engine to its adapters, built once per process."""

from functools import cache

import openai
from django.conf import settings

from claims_agent.adapters.openrouter import (
    OpenRouterChatModel,
    OpenRouterSpeechToText,
    OpenRouterTextToSpeech,
)
from claims_agent.engine import ConversationEngine, Voice
from claims_agent.ports import NullTelemetry, Telemetry
from claims_service.intake.repository import DjangoConversationRepository


@cache
def repository() -> DjangoConversationRepository:
    return DjangoConversationRepository()


@cache
def telemetry() -> Telemetry:
    if not settings.LANGFUSE_ENABLED:
        return NullTelemetry()
    from langfuse import get_client

    from claims_agent.adapters.langfuse_telemetry import LangfuseTelemetry

    return LangfuseTelemetry(get_client())


def chat_model() -> OpenRouterChatModel:
    """With Langfuse on, the OpenAI client is its wrapper, which records each completion as
    a generation (tokens and cost) under the current span."""
    client_class: type[openai.AsyncOpenAI] = openai.AsyncOpenAI
    if settings.LANGFUSE_ENABLED:
        from langfuse.openai import AsyncOpenAI

        client_class = AsyncOpenAI
    return OpenRouterChatModel.create(settings.OPENROUTER_API_KEY, client_class=client_class)


@cache
def engine() -> ConversationEngine:
    return ConversationEngine(llm=chat_model(), repository=repository(), telemetry=telemetry())


@cache
def voice() -> Voice:
    return Voice(
        OpenRouterSpeechToText(settings.OPENROUTER_API_KEY),
        OpenRouterTextToSpeech(settings.OPENROUTER_API_KEY),
        telemetry(),
    )
