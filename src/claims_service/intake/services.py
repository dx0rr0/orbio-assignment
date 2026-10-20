"""Wiring of the engine to its adapters, built once per process."""

from functools import cache

from django.conf import settings

from claims_agent.adapters.openrouter import (
    OpenRouterChatModel,
    OpenRouterSpeechToText,
    OpenRouterTextToSpeech,
)
from claims_agent.engine import ConversationEngine, Voice
from claims_service.intake.repository import DjangoConversationRepository


@cache
def repository() -> DjangoConversationRepository:
    return DjangoConversationRepository()


@cache
def engine() -> ConversationEngine:
    return ConversationEngine(
        llm=OpenRouterChatModel.create(settings.OPENROUTER_API_KEY), repository=repository()
    )


@cache
def voice() -> Voice:
    return Voice(
        OpenRouterSpeechToText(settings.OPENROUTER_API_KEY),
        OpenRouterTextToSpeech(settings.OPENROUTER_API_KEY),
    )
