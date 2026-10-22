"""JSON views of a conversation for the live panel and the API, and the events the server sends
over the conversation WebSocket (see `consumers`)."""

import base64
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import JsonValue

from claims_agent.domain import Claim, Conversation, Role
from claims_agent.engine import BotMessage
from claims_service.intake.error_messages import display_error


class FieldView(TypedDict):
    name: str
    state: Literal["empty", "valid", "invalid"]
    value: JsonValue
    error: str | None


class PendingFieldView(TypedDict):
    field: str
    last_error: str | None


class ClaimView(TypedDict):
    status: str
    urgency: str | None
    reference: str | None
    closure_reason: str | None
    pending_fields: list[PendingFieldView]
    fields: list[FieldView]


class TranscriptEntry(TypedDict):
    role: Literal["declarant", "agent"]
    text: str


class ConversationView(TypedDict):
    id: str
    claim: ClaimView
    summary: dict[str, JsonValue] | None
    transcript: list[TranscriptEntry]
    created_at: str
    updated_at: str


class ConversationStartedEvent(TypedDict):
    type: Literal["conversation_started"]
    conversation_id: str
    token: str


class SnapshotEvent(TypedDict):
    type: Literal["snapshot"]
    conversation: ConversationView


class TranscriptEvent(TypedDict):
    type: Literal["transcript"]
    text: str


class BotMessageEvent(TypedDict):
    type: Literal["bot_message"]
    text: str
    alert: bool


class ClaimUpdatedEvent(TypedDict):
    type: Literal["claim_updated"]
    claim: ClaimView
    summary: dict[str, JsonValue] | None


class AudioEvent(TypedDict):
    type: Literal["audio", "audio_continued"]
    mime: str
    data: str


class AudioEndEvent(TypedDict):
    type: Literal["audio_end"]


class ErrorEvent(TypedDict):
    type: Literal["error"]
    message: str


type ServerEvent = (
    ConversationStartedEvent
    | SnapshotEvent
    | TranscriptEvent
    | BotMessageEvent
    | ClaimUpdatedEvent
    | AudioEvent
    | AudioEndEvent
    | ErrorEvent
)


def claim_view(claim: Claim) -> ClaimView:
    """Each required field as empty / valid / invalid (with the reason), plus derived data."""
    data = claim.model_dump(mode="json")
    fields: list[FieldView] = []
    for name in claim.required_fields:
        value = data[name]
        state: Literal["empty", "valid", "invalid"]
        if value is not None:
            state, error = "valid", None
        elif name in claim.field_errors:
            state, error = "invalid", display_error(claim.field_errors[name])
        else:
            state, error = "empty", None
        fields.append({"name": name, "state": state, "value": value, "error": error})
    return {
        "status": claim.status.value,
        "urgency": claim.urgency.value if claim.urgency else None,
        "reference": claim.reference,
        "closure_reason": claim.closure_reason.value if claim.closure_reason else None,
        "pending_fields": [
            {
                "field": pending.field,
                "last_error": display_error(pending.last_error) if pending.last_error else None,
            }
            for pending in claim.pending_fields
        ],
        "fields": fields,
    }


def transcript_view(conversation: Conversation) -> list[TranscriptEntry]:
    return [
        {"role": "declarant" if m.role is Role.USER else "agent", "text": m.content or ""}
        for m in conversation.transcript
    ]


def conversation_view(conversation: Conversation) -> ConversationView:
    return {
        "id": str(conversation.id),
        "claim": claim_view(conversation.claim),
        "summary": conversation.summary.model_dump(mode="json") if conversation.summary else None,
        "transcript": transcript_view(conversation),
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def conversation_started_event(conversation_id: UUID, token: str) -> ConversationStartedEvent:
    return {"type": "conversation_started", "conversation_id": str(conversation_id), "token": token}


def snapshot_event(conversation: Conversation) -> SnapshotEvent:
    return {"type": "snapshot", "conversation": conversation_view(conversation)}


def transcript_event(text: str) -> TranscriptEvent:
    return {"type": "transcript", "text": text}


def bot_message_event(reply: BotMessage) -> BotMessageEvent:
    return {"type": "bot_message", "text": reply.text, "alert": reply.alert}


def claim_updated_event(conversation: Conversation) -> ClaimUpdatedEvent:
    summary = conversation.summary.model_dump(mode="json") if conversation.summary else None
    return {"type": "claim_updated", "claim": claim_view(conversation.claim), "summary": summary}


def audio_event(pcm: bytes, sample_rate: int, *, continued: bool) -> AudioEvent:
    """A piece of a reply's speech: the first starts the reply, the `continued` ones are played
    right after it."""
    return {
        "type": "audio_continued" if continued else "audio",
        "mime": f"audio/pcm;rate={sample_rate}",
        "data": base64.b64encode(pcm).decode("ascii"),
    }


def audio_end_event() -> AudioEndEvent:
    return {"type": "audio_end"}


def error_event(message: str) -> ErrorEvent:
    return {"type": "error", "message": message}
