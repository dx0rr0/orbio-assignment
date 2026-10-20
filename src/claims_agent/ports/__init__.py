"""Async interfaces the engine needs from the outside world; adapters implement them."""

from claims_agent.ports.llm import ChatModel, Completion, LLMError
from claims_agent.ports.repository import (
    ConcurrentUpdateError,
    ConversationNotFoundError,
    ConversationRepository,
)
from claims_agent.ports.speech import SpeechError, SpeechToText, TextToSpeech
from claims_agent.ports.telemetry import NullTelemetry, Span, Telemetry

__all__ = [
    "ChatModel",
    "Completion",
    "ConcurrentUpdateError",
    "ConversationNotFoundError",
    "ConversationRepository",
    "LLMError",
    "NullTelemetry",
    "Span",
    "SpeechError",
    "SpeechToText",
    "Telemetry",
    "TextToSpeech",
]
