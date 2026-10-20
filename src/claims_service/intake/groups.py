from uuid import UUID


def group_name(conversation_id: UUID) -> str:
    """Channel layer group of everyone watching a conversation."""
    return f"conversation-{conversation_id}"
