from typing import Literal, TypedDict
from uuid import UUID

from claims_service.intake.presenters import ClaimUpdatedEvent


def group_name(conversation_id: UUID) -> str:
    """Channel layer group of everyone watching a conversation."""
    return f"conversation-{conversation_id}"


class ClaimUpdatedMessage(TypedDict):
    """Sent to a group; Channels hands it to each consumer's `claim_updated`."""

    type: Literal["claim_updated"]
    payload: ClaimUpdatedEvent
