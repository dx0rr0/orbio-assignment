"""The LLM part of a turn: the LLM calls tools, round after round, until it replies in text or
the claim changes in a way only a template may announce."""

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from claims_agent.domain import Conversation, Message, Role, ToolCall
from claims_agent.engine import tools
from claims_agent.engine.identification import policy_of
from claims_agent.engine.prompts import system_prompt
from claims_agent.engine.scripted import needs_script
from claims_agent.engine.tool_handlers import ToolHandlers, Turn
from claims_agent.engine.tools import ToolResult
from claims_agent.ports import ChatModel, Completion, LLMError, PolicyCatalog

logger = logging.getLogger(__name__)

# LLM calls per turn: tool rounds plus the final reply.
MAX_LLM_ROUNDS = 4


class LLMUnavailableError(Exception):
    """No usable completion this turn, after retries. Carries the conversation as it was when
    the LLM failed, so claim updates from tools that already ran are not lost."""

    def __init__(self, conversation: Conversation) -> None:
        super().__init__("LLM unavailable")
        self.conversation = conversation


@dataclass
class _Progress:
    # The conversation after the last completed tool round, kept if the turn runs out of time.
    conversation: Conversation


def _prepared_reply(calls: Sequence[ToolCall], results: Sequence[ToolResult]) -> str | None:
    """The reply the LLM prepared with its updates, if every call was an update that was fully
    accepted. Any rejection or other tool needs the LLM to see the result before replying."""
    prepared = None
    for call, result in zip(calls, results, strict=True):
        if call.name != tools.UPDATE_CLAIM or result.error is not None or result.rejected:
            return None
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            return None
        reply = arguments.get(tools.REPLY_IF_ACCEPTED)
        if isinstance(reply, str) and reply.strip():
            prepared = reply.strip()
    return prepared


class ToolLoop:
    def __init__(
        self,
        *,
        llm: ChatModel,
        handlers: ToolHandlers,
        policies: PolicyCatalog,
        deadline: float,
    ) -> None:
        self._llm = llm
        self._handlers = handlers
        self._policies = policies
        self._deadline = deadline

    async def run(
        self, conversation: Conversation, now: datetime
    ) -> tuple[Conversation, str | None]:
        """Let the LLM call tools until it replies in text, or until the claim changes in a way
        a template must announce (then the reply is None). The reply is returned, not added to
        the history. Raises `LLMUnavailableError` without a usable reply within `deadline`
        seconds, retries included; tool results are kept even then."""
        turn = Turn(now)
        progress = _Progress(conversation)
        try:
            async with asyncio.timeout(self._deadline):
                return await self._rounds(conversation, turn, progress)
        except TimeoutError:
            logger.warning("LLM part of the turn exceeded %.0f s", self._deadline)
            raise LLMUnavailableError(progress.conversation) from None

    async def _rounds(
        self, conversation: Conversation, turn: Turn, progress: _Progress
    ) -> tuple[Conversation, str | None]:
        before = conversation.claim
        for _ in range(MAX_LLM_ROUNDS):
            completion = await self._complete(conversation, turn)
            if not completion.tool_calls:
                if not completion.content or not completion.content.strip():
                    logger.warning("LLM returned neither text nor tool calls")
                    raise LLMUnavailableError(conversation)
                return conversation, completion.content.strip()

            # Text that accompanies tool calls is dropped: it was never said to the declarant,
            # and the reply comes after the tools have run.
            conversation = conversation.add(
                Message(role=Role.ASSISTANT, tool_calls=completion.tool_calls)
            )
            results: list[ToolResult] = []
            for call in completion.tool_calls:
                if needs_script(before, conversation.claim):
                    # The declarant must hear the template before anything else happens to
                    # the claim (e.g. a confirmation of data they never heard read back).
                    result = ToolResult(error="not run: the claim changed first")
                else:
                    conversation, result = await self._handlers.run(conversation, call, turn)
                results.append(result)
                conversation = conversation.add(
                    Message(
                        role=Role.TOOL,
                        tool_call_id=call.id,
                        content=json.dumps(result.as_json(), ensure_ascii=False),
                    )
                )
            progress.conversation = conversation
            if needs_script(before, conversation.claim):
                return conversation, None
            if (prepared := _prepared_reply(completion.tool_calls, results)) is not None:
                return conversation, prepared

        logger.warning("LLM kept calling tools for %d rounds", MAX_LLM_ROUNDS)
        raise LLMUnavailableError(conversation)

    async def _complete(self, conversation: Conversation, turn: Turn) -> Completion:
        claim = conversation.claim
        system = system_prompt(claim, await policy_of(claim, self._policies), turn.now)
        try:
            return await self._llm.complete(
                system=system, messages=conversation.messages, tools=tools.TOOLS
            )
        except LLMError as error:
            raise LLMUnavailableError(conversation) from error
