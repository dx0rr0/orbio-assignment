"""OpenRouter adapters for the chat, speech-to-text and text-to-speech ports."""

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Self

import httpx
import httpx2
import openai
from openai.types import CompletionUsage, ReasoningEffort
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from claims_agent.domain import Message, Role, ToolCall
from claims_agent.ports import Completion, LLMError, SpeechError, ToolSpec, Usage

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_MODEL = "google/gemini-3.1-flash-lite"
# A turn's tool calls and short questions need no reasoning, and reasoning tokens come
# before the first token of the answer.
CHAT_REASONING_EFFORT: ReasoningEffort = "none"
STT_MODEL = "microsoft/mai-transcribe-2"
# About 1.5 s sooner to the whole reply than Gemini 3.8 Flash Lite TTS, and the locale of the
# voice gives it a Spain accent.
TTS_MODEL = "microsoft/mai-voice-2.1-flash"
TTS_VOICE = "es-ES-Marta:MAI-Voice-2.1-Flash"
# Asked for raw PCM (no decoding on the server): 16-bit mono at this rate.
TTS_SAMPLE_RATE = 24_000
TIMEOUT_SECONDS = 30.0
# Per chat request; the engine also bounds the whole turn (TURN_DEADLINE_SECONDS).
CHAT_TIMEOUT_SECONDS = 10.0
# Idle connections stay open between turns: the declarant usually talks for longer than
# httpx's default 5 s, and a new connection to openrouter.ai costs ~50 ms more (TCP and TLS),
# sometimes 0.5 s. OpenRouter kept idle connections for 80 s and more; closing them sooner on
# this side avoids sending a request on one it is closing.
KEEPALIVE_SECONDS = 60.0


def _to_openai(message: Message) -> ChatCompletionMessageParam:
    match message.role:
        case Role.USER:
            if message.content is None:
                raise ValueError("a user message needs content")
            return {"role": "user", "content": message.content}
        case Role.TOOL:
            if message.content is None or message.tool_call_id is None:
                raise ValueError("a tool message needs content and the id of its tool call")
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        case Role.ASSISTANT:
            converted: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": message.content,
            }
            if message.tool_calls:
                converted["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in message.tool_calls
                ]
            return converted


def _tool_to_openai(tool: ToolSpec) -> ChatCompletionToolParam:
    function = tool["function"]
    return {
        "type": "function",
        "function": {
            "name": function["name"],
            "description": function["description"],
            "parameters": dict(function["parameters"]),
        },
    }


class OpenRouterChatModel:
    """Chat completions with tools. Takes the client so tests can give it a fake transport;
    with Langfuse on, its OpenAI integration records each call as a generation."""

    def __init__(self, client: openai.AsyncOpenAI, model: str = CHAT_MODEL) -> None:
        self._client = client
        self._model = model

    @classmethod
    def create(cls, api_key: str, *, base_url: str = BASE_URL) -> "OpenRouterChatModel":
        client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=CHAT_TIMEOUT_SECONDS,
            max_retries=0,
            # The SDK's own client (httpx2) and pool sizes, with a longer keep-alive.
            http_client=openai.DefaultAsyncHttpxClient(
                limits=httpx2.Limits(
                    max_connections=openai.DEFAULT_CONNECTION_LIMITS.max_connections,
                    max_keepalive_connections=(
                        openai.DEFAULT_CONNECTION_LIMITS.max_keepalive_connections
                    ),
                    keepalive_expiry=KEEPALIVE_SECONDS,
                )
            ),
        )
        return cls(client)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> Completion:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, *map(_to_openai, messages)],
                tools=list(map(_tool_to_openai, tools)),
                temperature=0.3,
                reasoning_effort=CHAT_REASONING_EFFORT,
            )
        except openai.OpenAIError as error:
            raise LLMError(str(error)) from error
        if not response.choices:
            raise LLMError("completion without choices")
        message = response.choices[0].message
        tool_calls = tuple(
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
            for call in message.tool_calls or ()
            if call.type == "function"
        )
        return Completion(
            content=message.content, tool_calls=tool_calls, usage=_usage(response.usage)
        )

    async def aclose(self) -> None:
        await self._client.close()


def _usage(usage: CompletionUsage | None) -> Usage | None:
    if usage is None:
        return None
    details = usage.prompt_tokens_details
    return Usage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cached_input_tokens=(details.cached_tokens or 0) if details else 0,
    )


class _OpenRouterAudio:
    """Keeps one pooled client for its lifetime, so turns reuse its connections (no new TLS
    handshake per request); `aclose` closes it, a client passed in too."""

    def __init__(
        self, api_key: str, client: httpx.AsyncClient | None = None, *, base_url: str = BASE_URL
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=TIMEOUT_SECONDS,
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_SECONDS),
        )
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        with _speech_errors(path):
            response = await self._client.post(path, headers=self._headers, **kwargs)
            response.raise_for_status()
        return response


@contextmanager
def _speech_errors(path: str) -> Iterator[None]:
    try:
        yield
    except httpx.HTTPStatusError as error:
        # The body says why (OpenRouter's 402s name the limit that was hit).
        raise SpeechError(f"{path}: {error}: {error.response.text[:300]}") from error
    except httpx.HTTPError as error:
        raise SpeechError(f"{path}: {error}") from error


class OpenRouterSpeechToText(_OpenRouterAudio):
    async def transcribe(self, audio: bytes) -> str:
        # MAI Transcribe takes Ogg Opus as well as WAV (not WebM nor MP4); the browser sends
        # Ogg where it can encode it, ~12 times smaller.
        if audio.startswith(b"OggS"):
            upload = ("utterance.ogg", audio, "audio/ogg")
        else:
            upload = ("utterance.wav", audio, "audio/wav")
        response = await self._post(
            "/audio/transcriptions",
            data={"model": STT_MODEL, "language": "es"},
            files={"file": upload},
        )
        try:
            return str(response.json()["text"])
        except (ValueError, KeyError) as error:
            raise SpeechError(f"unexpected transcription response: {error}") from error


class OpenRouterTextToSpeech(_OpenRouterAudio):
    sample_rate = TTS_SAMPLE_RATE

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """MAI sends the audio progressively: for a 100-character sentence the first bytes
        arrive about 0.6 s after the request, the last about 0.8 s."""
        body = {"model": TTS_MODEL, "input": text, "voice": TTS_VOICE, "response_format": "pcm"}
        path = "/audio/speech"
        received = False
        with _speech_errors(path):
            async with self._client.stream(
                "POST", path, headers=self._headers, json=body
            ) as response:
                if response.is_error:
                    await response.aread()  # for the reason in its body
                    response.raise_for_status()
                async for piece in response.aiter_bytes():
                    received = True
                    yield piece
        if not received:
            raise SpeechError("empty speech response")
