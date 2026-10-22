"""The tool handlers on their own, without the LLM loop around them."""

import json

import pytest

from claims_agent.domain import ClaimStatus, ClosureReason, Conversation, ToolCall, close
from claims_agent.engine.tool_handlers import ToolHandlers, Turn
from claims_agent.engine.tools import CONFIRM_CLAIM, DECLARANT_REFUSES, UPDATE_CLAIM
from tests.support import IDENTIFICATION, NOW


@pytest.fixture
def handlers(repository, telemetry) -> ToolHandlers:
    return ToolHandlers(policies=repository, references=repository, telemetry=telemetry)


def tool_call(name: str, arguments: object = None) -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
    return ToolCall(id=f"call-{name}", name=name, arguments=raw)


async def test_an_update_validates_against_the_policy_catalog(handlers):
    conversation, result = await handlers.run(
        Conversation.start(NOW), tool_call(UPDATE_CLAIM, IDENTIFICATION), Turn(NOW)
    )

    assert conversation.claim.policy_number == "AUT-48213907"
    assert result.accepted == ("policy_number", "plate")
    assert result.status is ClaimStatus.IN_PROGRESS


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (tool_call(UPDATE_CLAIM, "{not json"), "arguments must be a JSON object"),
        (tool_call(UPDATE_CLAIM, "[]"), "arguments must be a JSON object"),
        (tool_call("delete_everything"), "unknown tool delete_everything"),
        (tool_call(CONFIRM_CLAIM), "the claim is not complete yet"),
    ],
)
async def test_a_call_that_cannot_run_is_an_error_for_the_llm(handlers, call, error):
    conversation = Conversation.start(NOW)

    after, result = await handlers.run(conversation, call, Turn(NOW))

    assert after == conversation
    assert result.error == error


async def test_no_tool_runs_on_a_closed_claim(handlers):
    claim = close(Conversation.start(NOW).claim, ClaimStatus.INCOMPLETE, ClosureReason.ABANDONED)
    conversation = Conversation.start(NOW).model_copy(update={"claim": claim})

    after, result = await handlers.run(conversation, tool_call(UPDATE_CLAIM), Turn(NOW))

    assert after == conversation
    assert result.error == "the claim is already closed"


async def test_a_refusal_counts_once_per_turn(handlers):
    refusal, turn = tool_call(DECLARANT_REFUSES, {"field": "declarant_name"}), Turn(NOW)

    conversation, first = await handlers.run(Conversation.start(NOW), refusal, turn)
    conversation, again = await handlers.run(conversation, refusal, turn)

    assert conversation.refusals == {"declarant_name": 1}
    assert first == again
    assert first.instruction is not None
    conversation, closing = await handlers.run(conversation, refusal, Turn(NOW))
    assert closing.status is ClaimStatus.INCOMPLETE
