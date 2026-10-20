"""Speech at the edges of a turn. Failures degrade instead of breaking the turn: a failed
transcription counts as an unintelligible utterance, a failed synthesis as a text-only reply."""

import logging
from collections.abc import Sequence

from claims_agent.engine.templates import BotMessage
from claims_agent.ports import NullTelemetry, SpeechError, SpeechToText, Telemetry, TextToSpeech

logger = logging.getLogger(__name__)


class Voice:
    def __init__(
        self, stt: SpeechToText, tts: TextToSpeech, telemetry: Telemetry | None = None
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._telemetry = telemetry or NullTelemetry()

    async def transcribe(self, wav: bytes) -> str:
        """The transcription, or "" if it failed (the engine then asks to repeat)."""
        with self._telemetry.span("stt", input={"wav_bytes": len(wav)}) as span:
            try:
                text = (await self._stt.transcribe(wav)).strip()
            except SpeechError as error:
                logger.exception("Transcription failed")
                span.update(metadata={"error": str(error)})
                return ""
            span.update(output=text)
            return text

    async def speak(self, replies: Sequence[BotMessage]) -> bytes | None:
        """One WAV with all the replies of a turn, or None if synthesis failed."""
        text = " ".join(reply.spoken for reply in replies)
        if not text:
            return None
        with self._telemetry.span("tts", input=text) as span:
            try:
                wav = await self._tts.synthesize(text)
            except SpeechError as error:
                logger.exception("Speech synthesis failed; replying in text only")
                span.update(metadata={"error": str(error)})
                return None
            span.update(output={"wav_bytes": len(wav)})
            return wav
