from typing import Protocol


class SpeechError(RuntimeError):
    """Transcription or synthesis failed."""


class SpeechToText(Protocol):
    async def transcribe(self, wav: bytes) -> str:
        """Transcribe Spanish speech from a WAV file. Raises `SpeechError`."""
        ...


class TextToSpeech(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Spanish speech for `text`, as a WAV file. Raises `SpeechError`."""
        ...
