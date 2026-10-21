"""Speech at the edges of a turn. Failures degrade instead of breaking the turn: a failed
transcription counts as an unintelligible utterance, a failed synthesis as a text-only reply."""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Sequence

from claims_agent.engine.templates import BotMessage
from claims_agent.ports import NullTelemetry, SpeechError, SpeechToText, Telemetry, TextToSpeech

logger = logging.getLogger(__name__)

# Where a spoken text may be cut into chunks: after the end of a sentence or a colon.
_CHUNK_BREAK = re.compile(r"(?<=[.:!?…])\s+")
# Shorter pieces ("Conductor:", "Gracias.") join the next one rather than being synthesized
# alone, which would sound clipped.
MIN_CHUNK_CHARS = 20


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


class Voice:
    def __init__(
        self, stt: SpeechToText, tts: TextToSpeech, telemetry: Telemetry | None = None
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._telemetry = telemetry or NullTelemetry()

    async def transcribe(self, wav: bytes) -> str:
        """The transcription, or "" if it failed (the engine then asks to repeat)."""
        return await self.transcribe_part(wav) or ""

    async def transcribe_part(self, wav: bytes) -> str | None:
        """The transcription of `wav`, a whole utterance or a part of one, or None if it failed.
        A silent part transcribes to ""."""
        with self._telemetry.span("stt", input={"wav_bytes": len(wav)}) as span:
            try:
                text = (await self._stt.transcribe(wav)).strip()
            except SpeechError as error:
                logger.exception("Transcription failed")
                span.update(metadata={"error": str(error)})
                return None
            span.update(output=text)
            return text

    @staticmethod
    def join_parts(parts: Sequence[str | None]) -> str:
        """The utterance spoken in `parts`, in order; "" if any part failed, since half an
        utterance could be taken for all of it."""
        if None in parts:
            return ""
        return " ".join(part for part in parts if part)

    async def speak(self, replies: Sequence[BotMessage]) -> bytes | None:
        """One WAV with all the replies of a turn, or None if synthesis failed."""
        text = " ".join(reply.spoken for reply in replies)
        return await self._synthesize(text) if text else None

    async def speak_in_chunks(self, replies: Sequence[BotMessage]) -> AsyncGenerator[bytes]:
        """The replies of a turn as WAV chunks, one per sentence, in order. Every chunk is
        synthesized at once and each is yielded as soon as it and those before it are ready, so
        the first sentence can play while the rest is synthesized. A failed synthesis ends the
        audio there (the text of the replies is sent anyway)."""
        texts = speech_chunks(" ".join(reply.spoken for reply in replies))
        synthesis = [asyncio.create_task(self._synthesize(text)) for text in texts]
        try:
            for task in synthesis:
                wav = await task
                if wav is None:
                    return
                yield wav
        finally:
            for task in synthesis:
                task.cancel()

    async def _synthesize(self, text: str) -> bytes | None:
        with self._telemetry.span("tts", input=text) as span:
            try:
                wav = await self._tts.synthesize(text)
            except SpeechError as error:
                logger.exception("Speech synthesis failed; replying in text only")
                span.update(metadata={"error": str(error)})
                return None
            span.update(output={"wav_bytes": len(wav)})
            return wav
