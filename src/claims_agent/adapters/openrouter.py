"""OpenRouter adapters for the chat, speech-to-text and text-to-speech ports."""

import io
import wave
from collections.abc import Sequence
from typing import Any, Self

import httpx
import openai
from openai.types import CompletionUsage
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

from claims_agent.domain import Message, Role, ToolCall
from claims_agent.ports import Completion, LLMError, SpeechError, ToolSpec, Usage

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_MODEL = "openai/gpt-6-luna"
STT_MODEL = "microsoft/mai-transcribe-2"
TTS_MODEL = "google/gemini-3.8-flash-lite-tts"
TTS_VOICE = "Kore"
# Gemini TTS only returns raw PCM: 16-bit mono at this rate.
TTS_SAMPLE_RATE = 24_000
TIMEOUT_SECONDS = 30.0
# Per chat request; the engine also bounds the whole turn (TURN_DEADLINE_SECONDS).
CHAT_TIMEOUT_SECONDS = 10.0


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
    """Chat completions with tools. Takes the client so that it can be the Langfuse-wrapped
    `AsyncOpenAI`, which records each call as a generation with tokens and cost."""

    def __init__(self, client: openai.AsyncOpenAI, model: str = CHAT_MODEL) -> None:
        self._client = client
        self._model = model

    @classmethod
    def create(
        cls, api_key: str, *, client_class: type[openai.AsyncOpenAI] = openai.AsyncOpenAI
    ) -> "OpenRouterChatModel":
        client = client_class(
            api_key=api_key, base_url=BASE_URL, timeout=CHAT_TIMEOUT_SECONDS, max_retries=0
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

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT_SECONDS)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.post(path, headers=self._headers, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            # The body says why (OpenRouter's 402s name the limit that was hit).
            raise SpeechError(f"{path}: {error}: {error.response.text[:300]}") from error
        except httpx.HTTPError as error:
            raise SpeechError(f"{path}: {error}") from error
        return response


class OpenRouterSpeechToText(_OpenRouterAudio):
    async def transcribe(self, wav: bytes) -> str:
        response = await self._post(
            "/audio/transcriptions",
            data={"model": STT_MODEL, "language": "es"},
            files={"file": ("utterance.wav", wav, "audio/wav")},
        )
        try:
            return str(response.json()["text"])
        except (ValueError, KeyError) as error:
            raise SpeechError(f"unexpected transcription response: {error}") from error


class OpenRouterTextToSpeech(_OpenRouterAudio):
    async def synthesize(self, text: str) -> bytes:
        response = await self._post(
            "/audio/speech",
            json={
                "model": TTS_MODEL,
                "input": text,
                "voice": TTS_VOICE,
                "response_format": "pcm",
            },
        )
        if not response.content:
            raise SpeechError("empty speech response")
        return pcm_to_wav(response.content, TTS_SAMPLE_RATE)


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
