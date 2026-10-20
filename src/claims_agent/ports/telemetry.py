from typing import Protocol


class Telemetry(Protocol):
    def score(self, name: str, value: float, comment: str | None = None) -> None:
        """Attach a score to the turn currently being traced."""
        ...


class NullTelemetry:
    def score(self, name: str, value: float, comment: str | None = None) -> None:
        pass
