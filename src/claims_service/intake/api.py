"""HTTP API. Conversation turns go over WebSocket; this is for reading conversations."""

from typing import Any
from uuid import UUID

from ninja import NinjaAPI
from ninja.errors import HttpError

from claims_agent.domain import ClaimStatus
from claims_agent.ports import ConversationNotFoundError
from claims_service.intake import services
from claims_service.intake.models import ConversationRecord
from claims_service.intake.presenters import conversation_view
from claims_service.intake.repository import to_conversation

api = NinjaAPI(title="Claims intake", version="0.1.0")


@api.get("/health")
async def health(request) -> dict[str, str]:
    return {"status": "ok"}


@api.get("/conversations")
async def list_conversations(
    request, status: ClaimStatus | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Most recent first, optionally by claim status."""
    records = ConversationRecord.objects.order_by("-updated_at")
    if status is not None:
        records = records.filter(status=status.value)
    return [
        conversation_view(to_conversation(record))
        async for record in records[: max(1, min(limit, 100))]
    ]


@api.get("/conversations/{conversation_id}")
async def get_conversation(request, conversation_id: UUID) -> dict[str, Any]:
    try:
        conversation = await services.repository().get(conversation_id)
    except ConversationNotFoundError:
        raise HttpError(404, "conversation not found") from None
    return conversation_view(conversation)
