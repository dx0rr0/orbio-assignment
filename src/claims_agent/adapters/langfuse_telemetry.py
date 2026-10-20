"""The telemetry port on Langfuse: session = conversation, trace = turn or background task.

LLM calls become generations (with tokens and cost) by building the chat model with
`langfuse.openai.AsyncOpenAI`; they nest under the current span through the OpenTelemetry
context, like the spans opened here.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse, propagate_attributes

from claims_agent.ports.telemetry import Span


class LangfuseTelemetry:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @contextmanager
    def trace(self, name: str, *, session_id: str, input: Any = None) -> Iterator[Span]:
        with (
            self._client.start_as_current_observation(name=name, input=input) as root,
            propagate_attributes(session_id=session_id, trace_name=name),
        ):
            yield root

    @contextmanager
    def span(self, name: str, *, input: Any = None) -> Iterator[Span]:
        with self._client.start_as_current_observation(name=name, input=input) as span:
            yield span

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        self._client.score_current_trace(name=name, value=value, comment=comment)

    def flush(self) -> None:
        self._client.flush()
