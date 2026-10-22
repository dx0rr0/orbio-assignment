"""Where conversations, policies and claim references come from: three ports, so that each
part of the engine depends only on the one it uses (one adapter may implement several)."""

from typing import Protocol
from uuid import UUID

from claims_agent.domain import Conversation, Policy


class ConversationNotFoundError(LookupError):
    pass


class ConcurrentUpdateError(RuntimeError):
    """The conversation was saved by someone else since it was loaded."""


class ConversationRepository(Protocol):
    async def create(self, conversation: Conversation) -> Conversation: ...

    async def get(self, conversation_id: UUID) -> Conversation:
        """Raises `ConversationNotFoundError`."""
        ...

    async def save(self, conversation: Conversation) -> Conversation:
        """Save if nobody saved it since it was loaded, and return it with its new version.
        Raises `ConcurrentUpdateError` otherwise."""
        ...


class PolicyCatalog(Protocol):
    """The insurer's policies, read-only: the engine validates claims against them."""

    async def get_policy(self, number: str) -> Policy | None: ...


class ReferenceSequence(Protocol):
    async def next_reference_sequence(self, year: int) -> int:
        """Next number of the yearly claim reference sequence, unique across processes."""
        ...
