import pytest

from claims_agent.engine import RetryingChatModel, tools
from claims_agent.engine.retrying import LLM_RETRY_DELAYS
from claims_agent.ports import LLMError
from tests.engine.fakes import LLM_DOWN, ScriptedLLM, say


class RecordedSleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def complete(llm: RetryingChatModel):
    return await llm.complete(system="sistema", messages=(), tools=tools.TOOLS)


async def test_a_transient_error_is_retried_after_a_wait():
    scripted, sleeps = ScriptedLLM().then(LLM_DOWN, say("Hola")), RecordedSleeps()

    completion = await complete(RetryingChatModel(scripted, sleep=sleeps))

    assert completion.content == "Hola"
    assert sleeps.delays == [LLM_RETRY_DELAYS[0]]
    # Each attempt is the same request.
    assert scripted.requests[0] == scripted.requests[1]


async def test_a_persistent_error_is_raised_after_every_wait():
    scripted, sleeps = (
        ScriptedLLM().then(*[LLM_DOWN] * (len(LLM_RETRY_DELAYS) + 1)),
        RecordedSleeps(),
    )

    with pytest.raises(LLMError):
        await complete(RetryingChatModel(scripted, sleep=sleeps))

    assert sleeps.delays == list(LLM_RETRY_DELAYS)
    assert len(scripted.requests) == len(LLM_RETRY_DELAYS) + 1


async def test_other_errors_are_not_retried():
    scripted, sleeps = ScriptedLLM().then(ValueError("bug")), RecordedSleeps()

    with pytest.raises(ValueError, match="bug"):
        await complete(RetryingChatModel(scripted, sleep=sleeps))

    assert sleeps.delays == []
