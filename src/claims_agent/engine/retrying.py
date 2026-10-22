"""Retries of a failed LLM call, as a chat model that wraps another one: the turn loop asks for
a completion once, and only hears of a failure that persisted."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence

from claims_agent.domain import Message
from claims_agent.ports import ChatModel, Completion, LLMError, ToolSpec

logger = logging.getLogger(__name__)

# Waits between attempts of a failed LLM call; one more attempt than waits.
LLM_RETRY_DELAYS = (0.5, 1.5)


class RetryingChatModel:
    """Retries `LLMError`s of `llm` with backoff and raises the last one when they persist. It
    has no deadline of its own: the turn bounds its LLM part as a whole, retries included."""

    def __init__(
        self,
        llm: ChatModel,
        *,
        delays: Sequence[float] = LLM_RETRY_DELAYS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._llm = llm
        self._delays = delays
        self._sleep = sleep

    async def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Completion:
        delays = iter(self._delays)
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._llm.complete(system=system, messages=messages, tools=tools)
            except LLMError as error:
                logger.warning("LLM call failed (attempt %d): %s", attempt, error)
                delay = next(delays, None)
                if delay is None:
                    raise
                await self._sleep(delay)
