"""Access to a conversation without accounts (v0): whoever started it gets a random token, and
only that token lets them resume it or read it. Only the token's hash is stored."""

import hashlib
import hmac
import secrets
from uuid import UUID

from claims_service.intake.models import ConversationRecord


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(conversation_id: UUID) -> str:
    token = secrets.token_urlsafe(32)
    await ConversationRecord.objects.filter(id=conversation_id).aupdate(
        access_token_hash=_hash(token)
    )
    return token


async def is_authorized(conversation_id: UUID, token: str | None) -> bool:
    """False for an unknown conversation too, so callers answer both the same way."""
    if not token:
        return False
    stored = (
        await ConversationRecord.objects.filter(id=conversation_id)
        .values_list("access_token_hash", flat=True)
        .afirst()
    )
    return bool(stored) and hmac.compare_digest(stored, _hash(token))


def bearer_token(authorization: str | None) -> str | None:
    """The token of an `Authorization: Bearer <token>` header."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None
