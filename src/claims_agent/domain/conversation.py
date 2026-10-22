"""A conversation with a declarant: the claim being collected and everything said so far."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from claims_agent.domain.claim import Claim, Urgency


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """A tool call as the LLM emitted it; `arguments` is the raw JSON text, which may be
    invalid, so that the engine can report the problem back to the LLM."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: str


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    # Assistant messages written by code from a template rather than by the LLM.
    scripted: bool = False

    @property
    def is_visible(self) -> bool:
        """Part of what the declarant and the agent said to each other (not tool traffic)."""
        return self.role is not Role.TOOL and bool(self.content)


class ClaimSummary(BaseModel):
    """Structured summary generated for the adjuster once the claim is confirmed."""

    model_config = ConfigDict(frozen=True)

    key_points: tuple[str, ...]
    urgency: Urgency
    next_steps: tuple[str, ...]


class Conversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    claim: Claim = Claim()
    messages: tuple[Message, ...] = ()
    # Consecutive turns that could not be understood, or in which the LLM was unavailable.
    unintelligible_streak: int = 0
    # The last of those whose speech could not be transcribed at all (an STT outage).
    transcription_failure_streak: int = 0
    llm_failure_streak: int = 0
    # How many times the declarant refused to give each field.
    refusals: dict[str, int] = {}
    # `Claim.fingerprint_to_confirm` of what the declarant was last asked to confirm (the
    # read-back, or a value that leaves the loss uncovered): only that can be confirmed.
    read_back_fingerprint: str | None = None
    # The claim as last read back: after a correction, only what differs from it is read.
    read_back_claim: Claim | None = None
    summary: ClaimSummary | None = None
    # The summary task gave up after its retries: an adjuster has to write it.
    summary_failed: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime
    # Optimistic concurrency token, bumped by the repository on every save.
    version: int = 0

    @classmethod
    def start(cls, now: datetime) -> "Conversation":
        return cls(created_at=now, updated_at=now)

    def add(self, *messages: Message) -> "Conversation":
        return self.model_copy(update={"messages": self.messages + messages})

    @property
    def transcript(self) -> tuple[Message, ...]:
        return tuple(m for m in self.messages if m.is_visible)
