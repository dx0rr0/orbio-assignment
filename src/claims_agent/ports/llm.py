from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from claims_agent.domain import Message, ToolCall


class LLMError(RuntimeError):
    """The LLM could not produce a completion (timeout, 5xx, rate limit…). Worth retrying."""


@dataclass(frozen=True)
class Completion:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()


class ChatModel(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
    ) -> Completion:
        """`tools` are OpenAI-style function definitions. Raises `LLMError`."""
        ...
