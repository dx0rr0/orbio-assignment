"""Fakes of the ports the engine drives, and helpers to script the LLM."""

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TypedDict

from pydantic import JsonValue

from claims_agent.domain import Message, ToolCall
from claims_agent.ports import Completion, LLMError, NullTelemetry, Span, ToolSpec


def say(text: str) -> Completion:
    return Completion(content=text)


def call(name: str, arguments: Mapping[str, JsonValue] | str = "{}", *, text: str | None = None):
    """A completion with one tool call; `arguments` as a mapping or as raw (maybe bad) JSON."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    tool_call = ToolCall(id=f"call-{name}", name=name, arguments=raw)
    return Completion(content=text, tool_calls=(tool_call,))


def calls(*completions: Completion) -> Completion:
    """One completion with the tool calls of several `call(...)`s, in order."""
    tool_calls = tuple(
        tc.model_copy(update={"id": f"call-{i}-{tc.name}"})
        for i, tc in enumerate(tc for c in completions for tc in c.tool_calls)
    )
    return Completion(content=None, tool_calls=tool_calls)


class LLMRequest(TypedDict):
    system: str
    messages: list[Message]
    tools: Sequence[ToolSpec]


class ScriptedLLM:
    """Returns the scripted completions (or raises the scripted errors) in order."""

    def __init__(self) -> None:
        self.script: list[Completion | Exception] = []
        self.requests: list[LLMRequest] = []

    def then(self, *steps: Completion | Exception) -> "ScriptedLLM":
        self.script.extend(steps)
        return self

    async def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Completion:
        self.requests.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.script:
            raise AssertionError("the LLM was called more times than scripted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class RecordingTelemetry(NullTelemetry):
    def __init__(self) -> None:
        self.scores: list[tuple[str, float, str | None]] = []
        self.spans: list[tuple[str, JsonValue]] = []

    @contextmanager
    def span(self, name: str, *, input: JsonValue = None) -> Iterator[Span]:
        self.spans.append((name, input))
        with super().span(name, input=input) as span:
            yield span

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        self.scores.append((name, value, comment))


async def no_sleep(_: float) -> None:
    pass


LLM_DOWN = LLMError("503 from provider")
