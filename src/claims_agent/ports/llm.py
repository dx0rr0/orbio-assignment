from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from claims_agent.domain import Message, ToolCall


class LLMError(RuntimeError):
    """The LLM could not produce a completion (timeout, 5xx, rate limit…). Worth retrying."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    # Input tokens the provider served from its prompt cache.
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage | None = None


class FunctionSpec(TypedDict):
    name: str
    description: str
    # JSON Schema of the arguments.
    parameters: Mapping[str, object]


class ToolSpec(TypedDict):
    """A function the LLM may call, in the function-calling format most providers accept."""

    type: Literal["function"]
    function: FunctionSpec


class ChatModel(Protocol):
    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> Completion:
        """Raises `LLMError`."""
        ...
