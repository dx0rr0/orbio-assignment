"""HTTP API. Conversation turns go over WebSocket; this is for reading conversations."""

from uuid import UUID

from django.conf import settings
from django.http import HttpRequest
from ninja import NinjaAPI
from ninja.errors import HttpError

from claims_agent.domain import ClaimStatus
from claims_agent.ports import ConversationNotFoundError
from claims_service.intake import access
from claims_service.intake.presenters import ConversationView, conversation_view
from claims_service.intake.repository import DjangoConversationRepository


def build_api(repository: DjangoConversationRepository) -> NinjaAPI:
    """The API, reading conversations from `repository` (`services.for_api` in the URLconf)."""
    api = NinjaAPI(title="Claims intake", version="0.1.0")

    @api.get("/health")
    async def health(request: HttpRequest) -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/conversations")
    async def list_conversations(
        request: HttpRequest, status: ClaimStatus | None = None, limit: int = 20
    ) -> list[ConversationView]:
        """Most recent first, optionally by claim status. Every conversation's personal data,
        so only for local development until there is real authentication."""
        if not settings.DEBUG:
            raise HttpError(404, "Not Found")
        conversations = await repository.list_recent(status=status, limit=max(1, min(limit, 100)))
        return [conversation_view(conversation) for conversation in conversations]

    @api.get("/conversations/{conversation_id}")
    async def get_conversation(request: HttpRequest, conversation_id: UUID) -> ConversationView:
        """Needs `Authorization: Bearer <token>`, with the token from `conversation_started`."""
        token = access.bearer_token(request.headers.get("Authorization"))
        if not await access.is_authorized(conversation_id, token):
            raise HttpError(404, "conversation not found")
        try:
            conversation = await repository.get(conversation_id)
        except ConversationNotFoundError:
            raise HttpError(404, "conversation not found") from None
        return conversation_view(conversation)

    return api
