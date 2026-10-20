"""In-process repository, for tests and for running the engine without a database (evals)."""

from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

from claims_agent.domain import Conversation, Policy
from claims_agent.ports import ConcurrentUpdateError, ConversationNotFoundError


class InMemoryConversationRepository:
    def __init__(self, policies: Iterable[Policy] = ()) -> None:
        self._conversations: dict[UUID, Conversation] = {}
        self._policies = {policy.number: policy for policy in policies}
        self._sequences: defaultdict[int, int] = defaultdict(int)

    async def create(self, conversation: Conversation) -> Conversation:
        self._conversations[conversation.id] = conversation
        return conversation

    async def get(self, conversation_id: UUID) -> Conversation:
        try:
            return self._conversations[conversation_id]
        except KeyError:
            raise ConversationNotFoundError(conversation_id) from None

    async def save(self, conversation: Conversation) -> Conversation:
        stored = await self.get(conversation.id)
        if stored.version != conversation.version:
            raise ConcurrentUpdateError(conversation.id)
        saved = conversation.model_copy(update={"version": conversation.version + 1})
        self._conversations[conversation.id] = saved
        return saved

    async def get_policy(self, number: str) -> Policy | None:
        return self._policies.get(number)

    async def next_reference_sequence(self, year: int) -> int:
        self._sequences[year] += 1
        return self._sequences[year]
