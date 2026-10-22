"""Speech at the edges of a turn. Failures degrade instead of breaking the turn: a failed
transcription is answered as an unintelligible utterance, a failed synthesis as a text-only reply.
Identifiers in whatever is synthesized are spelled out (`speech_text`). The speech of the fixed
messages (the greeting first) is synthesized once per process and replayed after that: instant
and free."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import aclosing

from claims_agent.engine.speech_text import spell_identifiers
from claims_agent.engine.templates import BotMessage, fixed_messages, greeting
from claims_agent.ports import NullTelemetry, SpeechError, SpeechToText, Telemetry, TextToSpeech

logger = logging.getLogger(__name__)

# Where a spoken text may be cut into chunks: after the end of a sentence. Not after a colon,
# which would part a label from its value ("Vehículo inmovilizado:" | "no."): the TTS paused
# there and clipped the next word.
_CHUNK_BREAK = re.compile(r"(?<=[.!?…])\s+")
# Shorter pieces ("Heridos: no.", "Gracias.") join the next one rather than being synthesized
# alone, which would sound clipped.
MIN_CHUNK_CHARS = 20
# Speech is sent on in pieces of at least this much audio rather than in every network read
# (a few kB): fewer messages, and the browser still starts playing almost as soon.
MIN_PIECE_SECONDS = 0.15
# Of 16-bit PCM.
SAMPLE_BYTES = 2
# Silence added between the sentences of a reply, on top of the ~0.35 s each synthesis ends
# with: without it a read-back runs its lines together, as if each full stop were a comma.
SENTENCE_PAUSE_SECONDS = 0.4


def speech_chunks(text: str) -> list[str]:
    """`text` cut into sentences, each at least MIN_CHUNK_CHARS long but for the last."""
    chunks: list[str] = []
    piece = ""
    for part in _CHUNK_BREAK.split(text.strip()):
        piece = f"{piece} {part}" if piece else part
        if len(piece) >= MIN_CHUNK_CHARS:
            chunks.append(piece)
            piece = ""
    if piece:
        if chunks:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


def _tts_text(replies: Sequence[BotMessage]) -> str:
    """What the TTS is given for the replies of a turn."""
    return " ".join(spell_identifiers(reply.spoken) for reply in replies)


def _fixed_texts() -> frozenset[str]:
    """What is synthesized for a fixed message said alone: all of it (`Voice.speak`) or each
    of its sentences (`Voice.speak_in_chunks`)."""
    wholes = [_tts_text([message]) for message in fixed_messages()]
    return frozenset(text for whole in wholes for text in (whole, *speech_chunks(whole)))


class Voice:
    def __init__(
        self, stt: SpeechToText, tts: TextToSpeech, telemetry: Telemetry | None = None
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._telemetry = telemetry or NullTelemetry()
        self._fixed = _fixed_texts()
        # The speech of fixed texts, as the pieces it was first yielded in. Only fixed texts:
        # the LLM's replies carry the declarant's data. A new TTS model or voice comes with a
        # restart, which empties it.
        self._kept: dict[str, tuple[bytes, ...]] = {}

    async def transcribe(self, audio: bytes) -> str | None:
        """The transcription, or None if it failed (the engine then asks to repeat)."""
        with self._telemetry.span("stt", input={"audio_bytes": len(audio)}) as span:
            try:
                text = (await self._stt.transcribe(audio)).strip()
            except SpeechError as error:
                logger.exception("Transcription failed")
                span.update(metadata={"error": str(error)})
                return None
            span.update(output=text)
            return text

    async def warm_up(self) -> None:
        """Synthesizes the greeting before the first conversation, so it is instant from the
        start. A failure is logged and left for the first conversation to retry."""
        async with aclosing(self.speak([greeting()])) as pieces:
            async for _ in pieces:
                pass

    @property
    def sample_rate(self) -> int:
        """Of the speech `speak` and `speak_in_chunks` yield: 16-bit mono PCM."""
        return self._tts.sample_rate

    async def speak(self, replies: Sequence[BotMessage]) -> AsyncGenerator[bytes]:
        """All the replies of a turn spoken in one synthesis, yielded in pieces as it arrives.
        A failed synthesis ends the audio there."""
        text = _tts_text(replies)
        async with aclosing(self._speak_in_order([text] if text else [])) as pieces:
            async for piece in pieces:
                yield piece

    async def speak_in_chunks(self, replies: Sequence[BotMessage]) -> AsyncGenerator[bytes]:
        """The replies of a turn spoken by sentence, yielded in pieces in order, with a pause
        between sentences. Every sentence is synthesized at once, and the pieces of each are
        yielded as they arrive once those of the sentences before it are out, so the first
        sentence plays while the rest is still being synthesized. A failed synthesis ends the
        audio there (the text of the replies is sent anyway)."""
        texts = speech_chunks(_tts_text(replies))
        async with aclosing(self._speak_in_order(texts)) as pieces:
            async for piece in pieces:
                yield piece

    async def _speak_in_order(self, texts: Sequence[str]) -> AsyncGenerator[bytes]:
        # Each synthesis fills its queue and ends it with None; a later sentence waits there
        # until those before it are out.
        queues = [asyncio.Queue[bytes | None]() for _ in texts]
        synthesis = [
            asyncio.create_task(self._synthesize(text, queue))
            for text, queue in zip(texts, queues, strict=True)
        ]
        try:
            for index, (task, queue) in enumerate(zip(synthesis, queues, strict=True)):
                if index:
                    yield self.sentence_pause
                while (piece := await queue.get()) is not None:
                    yield piece
                if not await task:
                    return
        finally:
            for task in synthesis:
                task.cancel()

    async def _synthesize(self, text: str, pieces: asyncio.Queue[bytes | None]) -> bool:
        """Puts the speech of `text` in `pieces`, then None; False if the synthesis failed."""
        if (kept := self._kept.get(text)) is not None:
            for piece in kept:
                pieces.put_nowait(piece)
            pieces.put_nowait(None)
            return True
        with self._telemetry.span("tts", input=text) as span:
            received: list[bytes] = []
            try:
                async for piece in whole_samples(self._tts.stream(text), self.min_piece_bytes):
                    pieces.put_nowait(piece)
                    received.append(piece)
            except SpeechError as error:
                logger.exception("Speech synthesis failed; replying in text only")
                span.update(metadata={"error": str(error)})
                return False
            finally:
                pieces.put_nowait(None)
            span.update(output={"pcm_bytes": sum(len(piece) for piece in received)})
            if text in self._fixed:
                self._kept[text] = tuple(received)
            return True

    @property
    def sentence_pause(self) -> bytes:
        return bytes(round(self.sample_rate * SENTENCE_PAUSE_SECONDS) * SAMPLE_BYTES)

    @property
    def min_piece_bytes(self) -> int:
        return round(self.sample_rate * MIN_PIECE_SECONDS) * SAMPLE_BYTES


async def whole_samples(chunks: AsyncIterator[bytes], min_bytes: int) -> AsyncGenerator[bytes]:
    """`chunks` regrouped into pieces of whole samples, each at least `min_bytes` long but for
    the last. A half sample at the very end is dropped."""
    buffer = bytearray()
    async for chunk in chunks:
        buffer += chunk
        if len(buffer) >= min_bytes:
            size = len(buffer) - len(buffer) % SAMPLE_BYTES
            yield bytes(buffer[:size])
            del buffer[:size]
    size = len(buffer) - len(buffer) % SAMPLE_BYTES
    if size:
        yield bytes(buffer[:size])
