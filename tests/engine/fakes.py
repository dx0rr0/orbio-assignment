import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from claims_agent.domain import MADRID, Message, ToolCall
from claims_agent.ports import Completion, LLMError, NullTelemetry, Span
from claims_agent.seed import seed_policies

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=MADRID)
HAPPY_POLICY, OTHER_DRIVER_POLICY, EXPIRED_POLICY, NEW_POLICY, _ = seed_policies(NOW.date())


# Scripted update_claim payloads for the happy-path policy.
IDENTIFICATION = {"policy_number": "AUT-48213907", "plate": "4721 KLM"}
COMPLETE = IDENTIFICATION | {
    "declarant_name": "María López Fernández",
    "driver_is_policyholder": True,
    "loss_type": "broken_glass",
    "loss_window": {"start": "2026-10-19T19:00", "end": "2026-10-19T19:00"},
    "loss_location": {
        "way": "A-3",
        "municipality": "Arganda del Rey",
        "province": "Madrid",
        "country": "España",
    },
    "description": "Una piedra que saltó de un camión rompió el parabrisas",
    "injuries": False,
    "vehicle_immobilized": False,
}


def say(text: str) -> Completion:
    return Completion(content=text)


def call(name: str, arguments: Mapping[str, Any] | str = "{}", *, text: str | None = None):
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


class ScriptedLLM:
    """Returns the scripted completions (or raises the scripted errors) in order."""

    def __init__(self) -> None:
        self.script: list[Completion | Exception] = []
        self.requests: list[dict[str, Any]] = []

    def then(self, *steps: Completion | Exception) -> "ScriptedLLM":
        self.script.extend(steps)
        return self

    async def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[Mapping[str, Any]]
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
        self.spans: list[tuple[str, Any]] = []

    @contextmanager
    def span(self, name: str, *, input: Any = None) -> Iterator[Span]:
        self.spans.append((name, input))
        with super().span(name, input=input) as span:
            yield span

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        self.scores.append((name, value, comment))


async def no_sleep(_: float) -> None:
    pass


LLM_DOWN = LLMError("503 from provider")
