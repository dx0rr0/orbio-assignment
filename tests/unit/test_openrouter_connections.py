import asyncio
import json
import re
from collections.abc import AsyncIterator

import pytest

from claims_agent.adapters.openrouter import OpenRouterChatModel, OpenRouterSpeechToText

# One body that both a transcription and a chat completion accept.
BODY = json.dumps(
    {
        "text": "hola",
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "?"}}
        ],
    }
).encode()
# Longer than httpx's default keep-alive (5 s), shorter than a declarant's turn.
IDLE_SECONDS = 5.5


class CountingServer:
    """A local HTTP/1.1 server that answers every request with BODY and counts the TCP
    connections it accepts."""

    def __init__(self) -> None:
        self.connections = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = re.search(rb"content-length: *(\d+)", head, re.IGNORECASE)
                await reader.readexactly(int(length[1]) if length else 0)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(BODY)}\r\n\r\n".encode()
                    + BODY
                )
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass  # the client closed the connection
        finally:
            writer.close()


@pytest.fixture
async def server() -> AsyncIterator[tuple[CountingServer, str]]:
    counting = CountingServer()
    listener = await asyncio.start_server(counting.handle, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    async with listener:
        yield counting, f"http://127.0.0.1:{port}"


async def test_connections_stay_open_between_turns(server):
    counting, base_url = server
    llm = OpenRouterChatModel.create("key", base_url=base_url)
    stt = OpenRouterSpeechToText("key", base_url=base_url)

    async def turn() -> None:
        await stt.transcribe(b"RIFF")
        await llm.complete(system="", messages=[], tools=[])

    async with stt:
        await turn()
        await asyncio.sleep(IDLE_SECONDS)
        await turn()
        await llm.aclose()

    # One connection per client, reused by the second turn.
    assert counting.connections == 2
