from collections.abc import AsyncIterator
from typing import Protocol


class SpeechError(RuntimeError):
    """Transcription or synthesis failed."""


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes) -> str:
        """Transcribe Spanish speech from a WAV or Ogg Opus file. Raises `SpeechError`."""
        ...

    async def warm_up(self) -> None:
        """Gets ready for the first transcription (e.g. opens the connection to the provider)
        without paying for one. Raises `SpeechError`."""
        ...


class TextToSpeech(Protocol):
    # Of the audio `stream` yields: raw PCM, 16-bit little-endian mono at this rate.
    @property
    def sample_rate(self) -> int: ...

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Spanish speech for `text`, yielded as it arrives from the provider, in pieces of
        any size (a piece may even end in the middle of a sample). Raises `SpeechError`, also
        after some pieces were yielded."""
        ...
