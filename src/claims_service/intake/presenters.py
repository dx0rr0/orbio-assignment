"""JSON views of a conversation for the live panel and the API."""

from typing import Any

from claims_agent.domain import Claim, Conversation, Role
from claims_agent.engine import BotMessage


def claim_view(claim: Claim) -> dict[str, Any]:
    """Each required field as empty / valid / invalid (with the reason), plus derived data."""
    data = claim.model_dump(mode="json")
    fields = []
    for name in claim.required_fields:
        value = data[name]
        if value is not None:
            state, error = "valid", None
        elif name in claim.field_errors:
            state, error = "invalid", claim.field_errors[name]
        else:
            state, error = "empty", None
        fields.append({"name": name, "state": state, "value": value, "error": error})
    return {
        "status": claim.status.value,
        "urgency": claim.urgency.value if claim.urgency else None,
        "reference": claim.reference,
        "closure_reason": claim.closure_reason.value if claim.closure_reason else None,
        "pending_fields": data["pending_fields"],
        "fields": fields,
    }


def transcript_view(conversation: Conversation) -> list[dict[str, str]]:
    return [
        {"role": "declarant" if m.role is Role.USER else "agent", "text": m.content or ""}
        for m in conversation.transcript
    ]


def conversation_view(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": str(conversation.id),
        "claim": claim_view(conversation.claim),
        "summary": conversation.summary.model_dump(mode="json") if conversation.summary else None,
        "transcript": transcript_view(conversation),
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def bot_message_event(reply: BotMessage) -> dict[str, Any]:
    return {"type": "bot_message", "text": reply.text}


def claim_updated_event(conversation: Conversation) -> dict[str, Any]:
    summary = conversation.summary.model_dump(mode="json") if conversation.summary else None
    return {"type": "claim_updated", "claim": claim_view(conversation.claim), "summary": summary}
