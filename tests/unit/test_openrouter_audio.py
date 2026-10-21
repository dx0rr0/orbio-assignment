import httpx
import pytest

from claims_agent.adapters.openrouter import BASE_URL, OpenRouterSpeechToText
from claims_agent.ports import SpeechError


async def test_a_provider_error_keeps_the_reason_from_the_response_body():
    body = '{"error": {"message": "This request requires at least $0.50 in balance for audio"}}'
    transport = httpx.MockTransport(lambda request: httpx.Response(402, text=body))
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)

    with pytest.raises(SpeechError, match=r"402.*at least \$0\.50 in balance"):
        await OpenRouterSpeechToText("key", client).transcribe(b"RIFF")
