"""Speech at the edges of a turn. Failures degrade instead of breaking the turn: a failed
transcription counts as an unintelligible utterance, a failed synthesis as a text-only reply."""

import logging
from collections.abc import Sequence

from claims_agent.engine.templates import BotMessage
from claims_agent.ports import SpeechError, SpeechToText, TextToSpeech

logger = logging.getLogger(__name__)


class Voice:
    def __init__(self, stt: SpeechToText, tts: TextToSpeech) -> None:
        self._stt = stt
        self._tts = tts

    async def transcribe(self, wav: bytes) -> str:
        """The transcription, or "" if it failed (the engine then asks to repeat)."""
        try:
            return (await self._stt.transcribe(wav)).strip()
        except SpeechError:
            logger.exception("Transcription failed")
            return ""

    async def speak(self, replies: Sequence[BotMessage]) -> bytes | None:
        """One WAV with all the replies of a turn, or None if synthesis failed."""
        text = " ".join(reply.spoken for reply in replies)
        if not text:
            return None
        try:
            return await self._tts.synthesize(text)
        except SpeechError:
            logger.exception("Speech synthesis failed; replying in text only")
            return None
