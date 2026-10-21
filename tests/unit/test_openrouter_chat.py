import json

import httpx
import openai
import pytest

from claims_agent.adapters.openrouter import BASE_URL, OpenRouterChatModel
from claims_agent.domain import Message, Role, ToolCall
from claims_agent.engine.tools import TOOLS
from claims_agent.ports import Usage

COMPLETION = {
    "id": "gen-1",
    "object": "chat.completion",
    "created": 0,
    "model": "test",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "¿Algo más?"},
        }
    ],
}


def chat_model(requests: list[dict], response: dict = COMPLETION) -> OpenRouterChatModel:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return OpenRouterChatModel(
        openai.AsyncOpenAI(api_key="key", base_url=BASE_URL, http_client=http_client)
    )


async def test_messages_and_tools_are_sent_in_the_openai_format():
    requests: list[dict] = []
    call = ToolCall(id="call-1", name="update_claim", arguments='{"injuries": false}')
    messages = [
        Message(role=Role.USER, content="Nadie herido"),
        Message(role=Role.ASSISTANT, tool_calls=(call,)),
        Message(role=Role.TOOL, tool_call_id="call-1", content='{"accepted": ["injuries"]}'),
    ]

    completion = await chat_model(requests).complete(
        system="Eres un asistente.", messages=messages, tools=TOOLS
    )

    assert completion.content == "¿Algo más?"
    [request] = requests
    assert request["messages"] == [
        {"role": "system", "content": "Eres un asistente."},
        {"role": "user", "content": "Nadie herido"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "update_claim", "arguments": '{"injuries": false}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"accepted": ["injuries"]}'},
    ]
    assert request["tools"] == list(TOOLS)


async def test_a_tool_message_without_its_call_id_is_not_sent():
    requests: list[dict] = []
    orphan = Message(role=Role.TOOL, content="{}")

    with pytest.raises(ValueError, match="id of its tool call"):
        await chat_model(requests).complete(system="", messages=[orphan], tools=[])

    assert requests == []


async def test_the_completion_carries_the_token_usage_including_cached_input():
    usage = {
        "prompt_tokens": 1200,
        "completion_tokens": 40,
        "total_tokens": 1240,
        "prompt_tokens_details": {"cached_tokens": 1024},
    }
    llm = chat_model([], COMPLETION | {"usage": usage})

    completion = await llm.complete(system="Eres un asistente.", messages=[], tools=())

    assert completion.usage == Usage(input_tokens=1200, output_tokens=40, cached_input_tokens=1024)


async def test_the_model_answers_without_reasoning():
    requests: list[dict] = []

    await chat_model(requests).complete(system="Eres un asistente.", messages=[], tools=TOOLS)

    [request] = requests
    assert request["reasoning_effort"] == "none"
