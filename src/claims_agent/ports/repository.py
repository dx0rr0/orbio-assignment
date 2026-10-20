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

    async def get_policy(self, number: str) -> Policy | None: ...

    async def next_reference_sequence(self, year: int) -> int:
        """Next number of the yearly claim reference sequence, unique across processes."""
        ...
