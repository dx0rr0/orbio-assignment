import json
from collections.abc import AsyncIterator

import httpx
import pytest

from claims_agent.adapters.openrouter import (
    BASE_URL,
    TTS_MODEL,
    TTS_VOICE,
    OpenRouterSpeechToText,
    OpenRouterTextToSpeech,
)
from claims_agent.ports import SpeechError


async def test_a_provider_error_keeps_the_reason_from_the_response_body():
    body = '{"error": {"message": "This request requires at least $0.50 in balance for audio"}}'
    transport = httpx.MockTransport(lambda request: httpx.Response(402, text=body))
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)

    with pytest.raises(SpeechError, match=r"402.*at least \$0\.50 in balance"):
        await OpenRouterSpeechToText("key", client).transcribe(b"RIFF")


async def test_the_adapter_keeps_its_client_open_until_closed():
    client = httpx.AsyncClient(base_url=BASE_URL)

    async with OpenRouterTextToSpeech("key", client):
        assert not client.is_closed

    assert client.is_closed


def speech_client(respond) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(respond))


async def body(*pieces: bytes, error: Exception | None = None) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece
    if error is not None:
        raise error


async def test_speech_is_streamed_as_it_arrives():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=body(b"\x01\x00\x02", b"\x00"))

    tts = OpenRouterTextToSpeech("key", speech_client(respond))

    pieces = [piece async for piece in tts.stream("Hola")]

    assert pieces == [b"\x01\x00\x02", b"\x00"]
    assert tts.sample_rate == 24_000
    assert json.loads(requests[0].content) == {
        "model": TTS_MODEL,
        "input": "Hola",
        "voice": TTS_VOICE,
        "response_format": "pcm",
    }


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(402, text='{"error": "at least $0.50"}'), r"402.*at least \$0\.50"),
        (httpx.Response(200, content=b""), "empty speech response"),
    ],
)
async def test_failed_speech_raises(response, reason):
    tts = OpenRouterTextToSpeech("key", speech_client(lambda _: response))

    with pytest.raises(SpeechError, match=reason):
        [piece async for piece in tts.stream("Hola")]


async def test_a_connection_lost_mid_speech_raises_after_what_arrived():
    cut = body(b"\x01\x00", error=httpx.ReadError("connection reset"))
    tts = OpenRouterTextToSpeech("key", speech_client(lambda _: httpx.Response(200, content=cut)))
    speech = tts.stream("Hola")

    assert await anext(speech) == b"\x01\x00"
    with pytest.raises(SpeechError, match="connection reset"):
        await anext(speech)
