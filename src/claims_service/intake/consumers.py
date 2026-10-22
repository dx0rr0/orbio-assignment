"""Conversation turns over WebSocket (ADR-0004).

Client → server:
    {"type": "resume", "token": "..."}                       first message when resuming
    {"type": "audio", "data": <base64 WAV or Ogg Opus>}      a spoken utterance
    {"type": "text", "text": "...", "reply_audio": false}    a typed one (dev mode, evals)
Server → client:
    conversation_started, snapshot, transcript, bot_message, claim_updated, audio,
    audio_continued, audio_end, error

The client's messages are typed in `protocol`; the server's events are built in `presenters`.
Every turn ends with `claim_updated`, a failed one too (after the apology), so the client knows
when it may speak again; a message that starts no turn gets an `error` instead.

The speech of a reply is sent in pieces as it arrives from the provider (sentences are
synthesized at once, but sent in order): `audio` carries the first piece and starts a new
reply, `audio_continued` the rest, to be played right after it. `data` is base64 raw PCM,
16-bit little-endian mono, at the rate in `mime` ("audio/pcm;rate=24000"). `audio_end` follows
the last piece, and ends every spoken turn: when synthesis failed it comes with no audio before.

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
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, aclosing
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from pydantic import JsonValue

from claims_agent.engine import TurnResult, templates
from claims_agent.ports import ConcurrentUpdateError, ConversationNotFoundError, Span
from claims_service.intake import error_messages
from claims_service.intake.groups import ClaimUpdatedMessage, group_name
from claims_service.intake.presenters import (
    audio_end_event,
    audio_event,
    bot_message_event,
    claim_updated_event,
    conversation_started_event,
    error_event,
    snapshot_event,
    transcript_event,
)
from claims_service.intake.protocol import (
    AudioMessage,
    ClientMessage,
    ResumeMessage,
    TextMessage,
    parse_client_message,
)
from claims_service.intake.services import ConsumerServices

# About a minute of 16 kHz mono 16-bit WAV, base64-encoded (Ogg Opus is far smaller).
MAX_AUDIO_BASE64_CHARS = 3_000_000
# Far more than anyone says in one turn; it bounds what a client can make the LLM read.
MAX_TEXT_CHARS = 2_000
NOT_FOUND_CLOSE_CODE = 4404
INTERNAL_ERROR_CLOSE_CODE = 1011

logger = logging.getLogger(__name__)


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    # How long a resuming client has to send its token.
    resume_timeout_s = 10.0
    # The conversation a connection asked to resume, until its `resume` message arrives.
    _resume_id: UUID | None = None
    _resume_deadline: asyncio.Task[None] | None = None
    # The connections to the providers (STT, LLM, TTS) are opened as the call starts (while
    # the greeting plays) and kept open with a free request this often, since OpenRouter
    # closes idle connections after 80-120 s; for as long as the declarant did something in
    # the last `keep_warm_s`.
    warm_up_every_s = 60.0
    keep_warm_s = 300.0
    _last_activity = 0.0
    _warm_connections: asyncio.Task[None] | None = None

    def __init__(
        self,
        *args: object,
        services: ConsumerServices,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._services = services
        # What the resume deadline and the warm-ups keep time with, so tests can step them.
        self._clock = clock
        self._sleep = sleep
        # The conversation this connection watches, once it started or resumed it.
        self._conversation_id: UUID | None = None

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
            self._keep_connections_warm()
            result = await self._services.engine.start()
            started = result.conversation.id
            token = await self._services.access.issue_token(started)
            await self._join(started)
            await self.send_json(conversation_started_event(started, token))
            with self._trace(started, "greeting"):
                await self._send_turn(result, reply_audio=True, greeting=True)
            return
        self._resume_id = conversation_id
        self._resume_deadline = asyncio.create_task(self._close_unless_resumed())

    async def _close_unless_resumed(self) -> None:
        await self._sleep(self.resume_timeout_s)
        self._resume_id = None
        await self.close(code=NOT_FOUND_CLOSE_CODE)

    async def _resume(self, message: ClientMessage | None) -> None:
        conversation_id, self._resume_id = self._resume_id, None
        self._cancel_resume_deadline()
        if conversation_id is None:
            return  # already refused, timed out or failed to start
        # A wrong token gets the same answer as an unknown id.
        if not isinstance(message, ResumeMessage) or not await self._services.access.is_authorized(
            conversation_id, message.token
        ):
            await self.close(code=NOT_FOUND_CLOSE_CODE)
            return
        try:
            conversation = await self._services.repository.get(conversation_id)
        except ConversationNotFoundError:
            await self.close(code=NOT_FOUND_CLOSE_CODE)
            return
        await self._join(conversation.id)
        self._keep_connections_warm()
        await self.send_json(snapshot_event(conversation))

    async def disconnect(self, code: int) -> None:
        self._cancel_resume_deadline()
        if self._warm_connections is not None:
            self._warm_connections.cancel()
        if self._conversation_id is not None:
            group = group_name(self._conversation_id)
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None, **kwargs: object
    ) -> None:
        # Channels raises on a binary frame or text that is not JSON, which would end the call;
        # they get the answer any other unreadable message gets.
        content: object = None
        if text_data:
            try:
                content = await self.decode_json(text_data)
            except json.JSONDecodeError:
                content = None
        await self.receive_json(content, **kwargs)

    async def receive_json(self, content: object, **kwargs: object) -> None:
        message = parse_client_message(content)
        conversation_id = self._conversation_id
        if conversation_id is None:
            # Until it watches a conversation, a connection can only resume one.
            try:
                await self._resume(message)
            except Exception:
                logger.exception("Could not resume the conversation", extra=self._log_context())
                await self._apologise()
                await self.close(code=INTERNAL_ERROR_CLOSE_CODE)
            return
        self._keep_connections_warm()
        try:
            await self._receive(conversation_id, message)
        except Exception:
            # The trace records the exception as it leaves it; the conversation is as it was
            # stored before this message, so the declarant can say it again.
            logger.exception("Could not handle the message", extra=self._log_context())
            await self._apologise()
            await self._end_failed_turn(conversation_id)

    async def _receive(self, conversation_id: UUID, message: ClientMessage | None) -> None:
        match message:
            case TextMessage(text=text, reply_audio=reply_audio):
                if len(text) > MAX_TEXT_CHARS:
                    await self._error(error_messages.TEXT_TOO_LONG)
                    return
                with self._trace(conversation_id, "turn", input={"text": text}) as trace:
                    await self._turn(conversation_id, text, reply_audio=reply_audio, trace=trace)
            case AudioMessage(data=data):
                audio = await self._decode_audio(data)
                if audio is None:
                    return
                with self._trace(
                    conversation_id, "turn", input={"audio_bytes": len(audio)}
                ) as trace:
                    utterance = await self._services.voice.transcribe(audio)
                    await self.send_json(transcript_event("" if utterance is None else utterance))
                    await self._turn(conversation_id, utterance, reply_audio=True, trace=trace)
            case _:
                # Not a message at all, or a `resume` once the connection watches one.
                await self._error(error_messages.UNREADABLE_MESSAGE)

    async def _turn(
        self, conversation_id: UUID, utterance: str | None, *, reply_audio: bool, trace: Span
    ) -> None:
        """`utterance` is None if the declarant's speech could not be transcribed."""
        try:
            result = await self._services.engine.handle_text(conversation_id, utterance)
        except ConcurrentUpdateError:
            # Another turn of the same conversation (e.g. a second tab) saved first.
            await self.send_json(bot_message_event(templates.technical_problem()))
            await self._end_failed_turn(conversation_id)
            return
        trace.update(
            output=[reply.text for reply in result.replies],
            metadata={"utterance": utterance, "status": result.conversation.claim.status.value},
        )
        await self._send_turn(result, reply_audio=reply_audio)
        if result.confirmed_claim:
            await self._request_summary(conversation_id)

    async def claim_updated(self, event: ClaimUpdatedMessage) -> None:
        """Pushed to the group by background tasks (summary, abandonment)."""
        await self.send_json(event["payload"])

    # --- helpers --------------------------------------------------------------------------

    async def _request_summary(self, conversation_id: UUID) -> None:
        # The claim is already confirmed and announced; a broker outage must not end the call,
        # and `queue_missing_summaries` queues it later. Publishing is blocking I/O, so it runs
        # off the event loop, and not in the thread the async ORM shares with other turns.
        try:
            await sync_to_async(self._services.request_summary, thread_sensitive=False)(
                str(conversation_id)
            )
        except Exception:
            logger.exception(
                "Could not queue the claim summary",
                extra={"conversation_id": str(conversation_id)},
            )

    def _keep_connections_warm(self) -> None:
        """On the declarant's activity: (re)starts keeping the provider connections open."""
        self._last_activity = self._clock()
        if self._warm_connections is None or self._warm_connections.done():
            self._warm_connections = asyncio.create_task(self._stay_warm())

    async def _stay_warm(self) -> None:
        while self._clock() - self._last_activity < self.keep_warm_s:
            await self._services.warm_up_connections()
            await self._sleep(self.warm_up_every_s)

    def _cancel_resume_deadline(self) -> None:
        if self._resume_deadline is not None:
            self._resume_deadline.cancel()
            self._resume_deadline = None

    def _trace(
        self, conversation_id: UUID, name: str, input: JsonValue = None
    ) -> AbstractContextManager[Span]:
        return self._services.telemetry.trace(name, session_id=str(conversation_id), input=input)

    async def _join(self, conversation_id: UUID) -> None:
        self._conversation_id = conversation_id
        await self.channel_layer.group_add(group_name(conversation_id), self.channel_name)

    async def _send_turn(
        self, result: TurnResult, *, reply_audio: bool, greeting: bool = False
    ) -> None:
        for reply in result.replies:
            await self.send_json(bot_message_event(reply))
        await self.send_json(claim_updated_event(result.conversation))
        if not reply_audio:
            return
        voice = self._services.voice
        # Nobody is waiting on an answer yet: the greeting is synthesized in one go.
        speech = voice.speak(result.replies) if greeting else voice.speak_in_chunks(result.replies)
        continued = False
        async with aclosing(speech) as pieces:
            async for pcm in pieces:
                await self.send_json(audio_event(pcm, voice.sample_rate, continued=continued))
                continued = True
        await self.send_json(audio_end_event())

    async def _decode_audio(self, data: str) -> bytes | None:
        """The audio file's bytes, or None after telling the client what was wrong."""
        if len(data) > MAX_AUDIO_BASE64_CHARS:
            await self._error(error_messages.AUDIO_TOO_LONG)
            return None
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error:
            await self._error(error_messages.UNREADABLE_AUDIO)
            return None

    def _log_context(self) -> dict[str, str | None]:
        conversation_id = self._conversation_id
        return {"conversation_id": None if conversation_id is None else str(conversation_id)}

    async def _apologise(self) -> None:
        """After an unexpected error, apologise from the template."""
        await self.send_json(bot_message_event(templates.technical_problem()))

    async def _end_failed_turn(self, conversation_id: UUID) -> None:
        """After the apology for a failed turn, the claim as stored: like every turn, it ends
        with `claim_updated`, which tells the client the agent is listening again."""
        try:
            conversation = await self._services.repository.get(conversation_id)
        except Exception:
            logger.exception("Could not reload the conversation", extra=self._log_context())
            return
        await self.send_json(claim_updated_event(conversation))

    async def _error(self, message: str) -> None:
        await self.send_json(error_event(message))
