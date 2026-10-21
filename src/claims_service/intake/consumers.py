"""Conversation turns over WebSocket (ADR-0004).

Client → server:
    {"type": "audio", "data": <base64 WAV>}                  a spoken utterance
    {"type": "text", "text": "...", "reply_audio": false}    a typed one (dev mode, evals)
Server → client:
    conversation_started, transcript, bot_message, claim_updated, audio, error

Connecting to /ws/conversations/ starts a conversation; /ws/conversations/<id>/ resumes
watching one (its snapshot is sent first). Channels hands this consumer one message at a time,
so turns of one connection never overlap.
"""

import base64
import binascii
import logging
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from claims_agent.engine import TurnResult
from claims_agent.ports import ConcurrentUpdateError, ConversationNotFoundError, Span
from claims_service.intake import services
from claims_service.intake.groups import group_name
from claims_service.intake.presenters import (
    bot_message_event,
    claim_updated_event,
    conversation_view,
)
from claims_service.intake.tasks import generate_claim_summary

# About a minute of 16 kHz mono 16-bit audio, base64-encoded.
MAX_AUDIO_BASE64_CHARS = 3_000_000
NOT_FOUND_CLOSE_CODE = 4404
# Give up queueing the summary after about a second instead of kombu's default retries.
SUMMARY_RETRY_POLICY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.5}

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    conversation_id: UUID

    async def connect(self) -> None:
        await self.accept()
        # The route's uuid converter already parsed it.
        conversation_id: UUID | None = self.scope["url_route"]["kwargs"].get("conversation_id")
        if conversation_id is None:
            result = await services.engine().start()
            await self._join(result.conversation.id)
            await self.send_json(
                {"type": "conversation_started", "conversation_id": str(self.conversation_id)}
            )
            with self._trace("greeting"):
                await self._send_turn(result, reply_audio=True)
            return
        try:
            conversation = await services.repository().get(conversation_id)
        except ConversationNotFoundError:
            await self.close(code=NOT_FOUND_CLOSE_CODE)
            return
        await self._join(conversation.id)
        await self.send_json({"type": "snapshot", "conversation": conversation_view(conversation)})

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "conversation_id"):
            group = group_name(self.conversation_id)
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if not isinstance(content, dict):
            await self._error("messages must be JSON objects")
            return
        match content.get("type"):
            case "text" if isinstance(content.get("text"), str):
                with self._trace("turn", input={"text": content["text"]}) as trace:
                    reply_audio = bool(content.get("reply_audio", False))
                    await self._turn(content["text"], reply_audio=reply_audio, trace=trace)
            case "audio" if isinstance(content.get("data"), str):
                wav = await self._decode_audio(content["data"])
                if wav is None:
                    return
                with self._trace("turn", input={"wav_bytes": len(wav)}) as trace:
                    utterance = await services.voice().transcribe(wav)
                    await self.send_json({"type": "transcript", "text": utterance})
                    await self._turn(utterance, reply_audio=True, trace=trace)
            case _:
                await self._error('expected {"type": "text", "text": ...} or audio')

    async def _turn(self, utterance: str, *, reply_audio: bool, trace: Span) -> None:
        try:
            result = await services.engine().handle_text(self.conversation_id, utterance)
        except ConcurrentUpdateError:
            await self._error("the conversation changed meanwhile; please repeat")
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
            await sync_to_async(generate_claim_summary.apply_async, thread_sensitive=False)(
                args=[str(self.conversation_id)], retry_policy=SUMMARY_RETRY_POLICY
            )
        except Exception:
            logger.exception(
                "Could not queue the claim summary",
                extra={"conversation_id": str(self.conversation_id)},
            )

    def _trace(self, name: str, input: Any = None) -> AbstractContextManager[Span]:
        return services.telemetry().trace(name, session_id=str(self.conversation_id), input=input)

    async def _join(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        await self.channel_layer.group_add(group_name(conversation_id), self.channel_name)

    async def _send_turn(self, result: TurnResult, *, reply_audio: bool) -> None:
        for reply in result.replies:
            await self.send_json(bot_message_event(reply))
        await self.send_json(claim_updated_event(result.conversation))
        if reply_audio:
            wav = await services.voice().speak(result.replies)
            if wav is not None:
                data = base64.b64encode(wav).decode("ascii")
                await self.send_json({"type": "audio", "mime": "audio/wav", "data": data})

    async def _decode_audio(self, data: str) -> bytes | None:
        """The WAV bytes, or None after telling the client what was wrong."""
        if len(data) > MAX_AUDIO_BASE64_CHARS:
            await self._error("audio is too long; keep utterances under a minute")
            return None
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error:
            await self._error("audio data must be base64")
            return None

    async def _error(self, message: str) -> None:
        await self.send_json({"type": "error", "message": message})
