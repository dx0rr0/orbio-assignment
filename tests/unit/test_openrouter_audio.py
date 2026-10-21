import httpx
import pytest

from claims_agent.adapters.openrouter import (
    BASE_URL,
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


def transcriber(uploads: list[httpx.Request]) -> OpenRouterSpeechToText:
    def handle(request: httpx.Request) -> httpx.Response:
        uploads.append(request)
        return httpx.Response(200, json={"text": "hola"})

    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle))
    return OpenRouterSpeechToText("key", client)


@pytest.mark.parametrize(
    ("audio", "part_header"),
    [
        (b"RIFF\x00\x00\x00\x00WAVE", b'filename="utterance.wav"\r\nContent-Type: audio/wav'),
        (b"OggS\x00\x02", b'filename="utterance.ogg"\r\nContent-Type: audio/ogg'),
    ],
)
async def test_the_upload_is_named_after_its_format(audio, part_header):
    uploads: list[httpx.Request] = []

    assert await transcriber(uploads).transcribe(audio) == "hola"

    [upload] = uploads
    body = upload.read()
    assert part_header in body
    assert audio in body


async def test_the_adapter_keeps_its_client_open_until_closed():
    client = httpx.AsyncClient(base_url=BASE_URL)

    async with OpenRouterTextToSpeech("key", client):
        assert not client.is_closed

    assert client.is_closed
