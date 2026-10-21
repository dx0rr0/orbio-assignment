"""Async interfaces the engine needs from the outside world; adapters implement them."""

from claims_agent.ports.llm import ChatModel, Completion, FunctionSpec, LLMError, ToolSpec
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
    "FunctionSpec",
    "LLMError",
    "NullTelemetry",
    "Span",
    "SpeechError",
    "SpeechToText",
    "Telemetry",
    "TextToSpeech",
    "ToolSpec",
]
