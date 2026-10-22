from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from pydantic import JsonValue


class Span(Protocol):
    # The result is ignored (Langfuse's spans return themselves).
    def update(self, *, output: JsonValue = None, metadata: JsonValue = None) -> object: ...


class Telemetry(Protocol):
    def trace(
        self, name: str, *, session_id: str, input: JsonValue = None
    ) -> AbstractContextManager[Span]:
        """Start a trace (one turn, one background task) within a session (a conversation)."""
        ...

    def span(self, name: str, *, input: JsonValue = None) -> AbstractContextManager[Span]:
        """A step of the current trace."""
        ...

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        """Attach a score to the current trace."""
        ...

    def flush(self) -> None:
        """Send what is buffered, for processes that may exit before the exporter runs."""
        ...


class _NullSpan:
    def update(self, *, output: JsonValue = None, metadata: JsonValue = None) -> None:
        pass


class NullTelemetry:
    @contextmanager
    def trace(self, name: str, *, session_id: str, input: JsonValue = None) -> Iterator[Span]:
        yield _NullSpan()

    @contextmanager
    def span(self, name: str, *, input: JsonValue = None) -> Iterator[Span]:
        yield _NullSpan()

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        pass

    def flush(self) -> None:
        pass
