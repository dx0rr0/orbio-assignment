"""Conversation turns over WebSocket (ADR-0004).

Client → server:
    {"type": "resume", "token": "..."}                       first message when resuming
    {"type": "audio", "data": <base64 WAV>}                  a spoken utterance
    {"type": "text", "text": "...", "reply_audio": false}    a typed one (dev mode, evals)
Server → client:
    conversation_started, snapshot, transcript, bot_message, claim_updated, audio, error

Connecting to /ws/conversations/ starts a conversation (conversation_started carries its
access token). Connecting to /ws/conversations/<id>/ resumes watching one: the first message
must be `resume` with that token, and the snapshot follows. The token is not in the URL because
URLs end up in server and proxy logs. A wrong or missing token, an unknown id, any other first
message and no handshake within `resume_timeout_s` all close with NOT_FOUND_CLOSE_CODE, so
nobody learns which conversation ids exist. Channels hands this consumer one message at a time,
so turns of one connection never overlap.
"""

import asyncio
import base64
import binascii
import logging
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from claims_agent.engine import TurnResult, templates
from claims_agent.ports import ConcurrentUpdateError, ConversationNotFoundError, Span
from claims_service.intake import error_messages
from claims_service.intake.groups import group_name
from claims_service.intake.presenters import (
    bot_message_event,
    claim_updated_event,
    conversation_view,
)
from claims_service.intake.services import ConsumerServices

# About a minute of 16 kHz mono 16-bit audio, base64-encoded.
MAX_AUDIO_BASE64_CHARS = 3_000_000
# Far more than anyone says in one turn; it bounds what a client can make the LLM read.
MAX_TEXT_CHARS = 2_000
NOT_FOUND_CLOSE_CODE = 4404
INTERNAL_ERROR_CLOSE_CODE = 1011

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    conversation_id: UUID
    # How long a resuming client has to send its token.
    resume_timeout_s = 10.0
    # The conversation a connection asked to resume, until its `resume` message arrives.
    _resume_id: UUID | None = None
    _resume_deadline: asyncio.Task[None] | None = None

    def __init__(self, *args: Any, services: ConsumerServices, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._services = services

    async def connect(self) -> None:
        await self.accept()
        try:
            await self._open()
        except Exception:
            # Nothing to resume without a conversation: apologise and hang up.
            logger.exception("Could not open the conversation", extra=self._log_context())
            await self._apologise()
            await self.close(code=INTERNAL_ERROR_CLOSE_CODE)

    async def _open(self) -> None:
        # The route's uuid converter already parsed it.
        conversation_id: UUID | None = self.scope["url_route"]["kwargs"].get("conversation_id")
        if conversation_id is None:
            result = await self._services.engine.start()
            token = await self._services.access.issue_token(result.conversation.id)
            await self._join(result.conversation.id)
            await self.send_json(
                {
                    "type": "conversation_started",
                    "conversation_id": str(self.conversation_id),
                    "token": token,
                }
            )
            with self._trace("greeting"):
                await self._send_turn(result, reply_audio=True)
            return
        self._resume_id = conversation_id
        self._resume_deadline = asyncio.create_task(self._close_unless_resumed())

    async def _close_unless_resumed(self) -> None:
        await asyncio.sleep(self.resume_timeout_s)
        self._resume_id = None
        await self.close(code=NOT_FOUND_CLOSE_CODE)

    async def _resume(self, content: Any) -> None:
        conversation_id, self._resume_id = self._resume_id, None
        self._cancel_resume_deadline()
        if conversation_id is None:
            return  # already refused, timed out or failed to start
        is_resume = isinstance(content, dict) and content.get("type") == "resume"
        token = content.get("token") if is_resume else None
        # A wrong token gets the same answer as an unknown id.
        if not isinstance(token, str) or not await self._services.access.is_authorized(
            conversation_id, token
        ):
            await self.close(code=NOT_FOUND_CLOSE_CODE)
            return
        try:
            conversation = await self._services.repository.get(conversation_id)
        except ConversationNotFoundError:
            await self.close(code=NOT_FOUND_CLOSE_CODE)
            return
        await self._join(conversation.id)
        await self.send_json({"type": "snapshot", "conversation": conversation_view(conversation)})

    async def disconnect(self, code: int) -> None:
        self._cancel_resume_deadline()
        if hasattr(self, "conversation_id"):
            group = group_name(self.conversation_id)
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if not hasattr(self, "conversation_id"):
            # Until it watches a conversation, a connection can only resume one.
            try:
                await self._resume(content)
            except Exception:
                logger.exception("Could not resume the conversation", extra=self._log_context())
                await self._apologise()
                await self.close(code=INTERNAL_ERROR_CLOSE_CODE)
            return
        try:
            await self._receive(content)
        except Exception:
            # The trace records the exception as it leaves it; the conversation is as it was
            # stored before this message, so the declarant can say it again.
            logger.exception("Could not handle the message", extra=self._log_context())
            await self._apologise()

    async def _receive(self, content: Any) -> None:
        if not isinstance(content, dict):
            await self._error(error_messages.UNREADABLE_MESSAGE)
            return
        match content.get("type"):
            case "text" if isinstance(content.get("text"), str):
                if len(content["text"]) > MAX_TEXT_CHARS:
                    await self._error(error_messages.TEXT_TOO_LONG)
                    return
                with self._trace("turn", input={"text": content["text"]}) as trace:
                    reply_audio = bool(content.get("reply_audio", False))
                    await self._turn(content["text"], reply_audio=reply_audio, trace=trace)
            case "audio" if isinstance(content.get("data"), str):
                wav = await self._decode_audio(content["data"])
                if wav is None:
                    return
                with self._trace("turn", input={"wav_bytes": len(wav)}) as trace:
                    utterance = await self._services.voice.transcribe(wav)
                    await self.send_json({"type": "transcript", "text": utterance})
                    await self._turn(utterance, reply_audio=True, trace=trace)
            case _:
                await self._error(error_messages.UNREADABLE_MESSAGE)

    async def _turn(self, utterance: str, *, reply_audio: bool, trace: Span) -> None:
        try:
            result = await self._services.engine.handle_text(self.conversation_id, utterance)
        except ConcurrentUpdateError:
            # Another turn of the same conversation (e.g. a second tab) saved first.
            await self.send_json(bot_message_event(templates.technical_problem()))
            return
        trace.update(
            output=[reply.text for reply in result.replies],
            metadata={"utterance": utterance, "status": result.conversation.claim.status.value},
        )
        await self._send_turn(result, reply_audio=reply_audio)
        if result.confirmed_claim:
            await self._request_summary()

    async def claim_updated(self, event: dict[str, Any]) -> None:
        """Pushed to the group by background tasks (summary, abandonment)."""
        await self.send_json(event["payload"])

    # --- helpers --------------------------------------------------------------------------

    async def _request_summary(self) -> None:
        # The claim is already confirmed and announced; a broker outage must not end the call,
        # and `queue_missing_summaries` queues it later. Publishing is blocking I/O, so it runs
        # off the event loop, and not in the thread the async ORM shares with other turns.
        try:
            await sync_to_async(self._services.request_summary, thread_sensitive=False)(
                str(self.conversation_id)
            )
        except Exception:
            logger.exception(
                "Could not queue the claim summary",
                extra={"conversation_id": str(self.conversation_id)},
            )

    def _cancel_resume_deadline(self) -> None:
        if self._resume_deadline is not None:
            self._resume_deadline.cancel()
            self._resume_deadline = None

    def _trace(self, name: str, input: Any = None) -> AbstractContextManager[Span]:
        return self._services.telemetry.trace(
            name, session_id=str(self.conversation_id), input=input
        )

    async def _join(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        await self.channel_layer.group_add(group_name(conversation_id), self.channel_name)

    async def _send_turn(self, result: TurnResult, *, reply_audio: bool) -> None:
        for reply in result.replies:
            await self.send_json(bot_message_event(reply))
        await self.send_json(claim_updated_event(result.conversation))
        if reply_audio:
            wav = await self._services.voice.speak(result.replies)
            if wav is not None:
                data = base64.b64encode(wav).decode("ascii")
                await self.send_json({"type": "audio", "mime": "audio/wav", "data": data})

    async def _decode_audio(self, data: str) -> bytes | None:
        """The WAV bytes, or None after telling the client what was wrong."""
        if len(data) > MAX_AUDIO_BASE64_CHARS:
            await self._error(error_messages.AUDIO_TOO_LONG)
            return None
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error:
            await self._error(error_messages.UNREADABLE_AUDIO)
            return None

    def _log_context(self) -> dict[str, str | None]:
        conversation_id = getattr(self, "conversation_id", None)
        return {"conversation_id": conversation_id and str(conversation_id)}

    async def _apologise(self) -> None:
        """After an unexpected error, apologise from the template."""
        await self.send_json(bot_message_event(templates.technical_problem()))

    async def _error(self, message: str) -> None:
        await self.send_json({"type": "error", "message": message})
