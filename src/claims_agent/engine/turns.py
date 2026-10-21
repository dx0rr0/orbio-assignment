"""The turn loop: the LLM converses and proposes values through tools; code validates them,
moves the claim between statuses and writes every critical message (ADR-0002).

Each turn loads the conversation, processes the declarant's utterance and saves it, so any
server process can handle any turn.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from claims_agent.domain import (
    MADRID,
    Claim,
    ClaimStatus,
    ClosureReason,
    Conversation,
    Message,
    Policy,
    Role,
    ToolCall,
    apply_update,
    close,
    confirm,
    format_reference,
    identified_policy,
    policy_number_to_look_up,
)
from claims_agent.engine import templates, tools
from claims_agent.engine.hallucinations import check_reply
from claims_agent.engine.prompts import system_prompt
from claims_agent.engine.templates import BotMessage
from claims_agent.ports import (
    ChatModel,
    Completion,
    ConcurrentUpdateError,
    ConversationRepository,
    LLMError,
    NullTelemetry,
    Telemetry,
)

logger = logging.getLogger(__name__)

# Unintelligible utterances in a row before the claim is escalated.
MAX_REPEAT_REQUESTS = 3
# Turns in a row without a usable LLM reply before the claim is escalated.
MAX_LLM_FAILURES = 3
# Times the declarant may refuse a field: the first gets an explanation, the second closes.
MAX_REFUSALS = 2
# LLM calls per turn: tool rounds plus the final reply.
MAX_LLM_ROUNDS = 4
# Waits between attempts of a failed LLM call; one more attempt than waits.
LLM_RETRY_DELAYS = (0.5, 1.5)
# Seconds the LLM part of a turn may take, retries included, before the agent apologises.
TURN_DEADLINE_SECONDS = 25.0

_WORD = re.compile(r"\w{2,}")


@dataclass(frozen=True)
class TurnResult:
    conversation: Conversation
    replies: tuple[BotMessage, ...]
    # The claim status before the turn, to tell what the turn changed.
    previous_status: ClaimStatus = ClaimStatus.IN_PROGRESS

    @property
    def confirmed_claim(self) -> bool:
        """Whether this turn confirmed the claim."""
        return (
            self.previous_status is not ClaimStatus.CONFIRMED
            and self.conversation.claim.status is ClaimStatus.CONFIRMED
        )


@dataclass
class _Turn:
    """What one declarant turn has done so far, so that a value the LLM sends again within the
    turn counts as one answer (one failed attempt, one refusal)."""

    now: datetime
    # The conversation after the last completed tool round, kept if the turn runs out of time.
    progress: Conversation | None = None
    failed_fields: set[str] = field(default_factory=set)
    refused_fields: set[str] = field(default_factory=set)


class _LLMUnavailableError(Exception):
    """No usable completion this turn, after retries. Carries the conversation as it was when
    the LLM failed, so claim updates from tools that already ran are not lost."""

    def __init__(self, conversation: Conversation) -> None:
        super().__init__("LLM unavailable")
        self.conversation = conversation


def is_unintelligible(utterance: str) -> bool:
    """Nothing worth sending to the LLM: empty, noise, or punctuation only."""
    return _WORD.search(utterance) is None


def _needs_script(before: Claim, after: Claim) -> bool:
    """Whether the claim changed in a way only a template may announce."""
    if after.status.is_final:
        return True
    return after.status is ClaimStatus.PENDING_CONFIRMATION and (
        before.status is not ClaimStatus.PENDING_CONFIRMATION
        or before.data_fingerprint != after.data_fingerprint
    )


class ConversationEngine:
    def __init__(
        self,
        *,
        llm: ChatModel,
        repository: ConversationRepository,
        telemetry: Telemetry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(MADRID),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        turn_deadline: float = TURN_DEADLINE_SECONDS,
    ) -> None:
        self._llm = llm
        self._repository = repository
        self._telemetry = telemetry or NullTelemetry()
        self._clock = clock
        self._sleep = sleep
        self._turn_deadline = turn_deadline

    async def start(self) -> TurnResult:
        conversation, replies = _say(Conversation.start(self._clock()), templates.greeting())
        return TurnResult(await self._repository.create(conversation), replies)

    async def handle_text(self, conversation_id: UUID, utterance: str) -> TurnResult:
        now = self._clock()
        conversation = await self._repository.get(conversation_id)
        if not conversation.claim.status.is_final:
            # Mark the activity before the slow part of the turn, so that the abandonment
            # sweep leaves the conversation alone while the turn is being processed.
            try:
                conversation = await self._repository.save(
                    conversation.model_copy(update={"updated_at": now})
                )
            except ConcurrentUpdateError:
                # Saved meanwhile, e.g. closed by the sweep right after we loaded it.
                conversation = await self._repository.get(conversation_id)
        if conversation.claim.status.is_final:
            status = conversation.claim.status
            reply = templates.already_closed(conversation.claim)
            return TurnResult(conversation, (reply,), previous_status=status)

        previous_status = conversation.claim.status
        if is_unintelligible(utterance):
            conversation, replies = self._unintelligible(conversation)
        else:
            conversation = conversation.add(Message(role=Role.USER, content=utterance.strip()))
            conversation = conversation.model_copy(update={"unintelligible_streak": 0})
            conversation, replies = await self._converse(conversation, _Turn(now))

        conversation = conversation.model_copy(update={"updated_at": now})
        try:
            saved = await self._repository.save(conversation)
        except ConcurrentUpdateError:
            stored = await self._repository.get(conversation_id)
            if not stored.claim.status.is_final:
                raise
            # Closed by someone else while this turn ran: that is what the declarant must hear.
            reply = templates.already_closed(stored.claim)
            return TurnResult(stored, (reply,), previous_status=stored.claim.status)
        return TurnResult(saved, replies, previous_status=previous_status)

    # --- turn kinds -----------------------------------------------------------------------

    def _unintelligible(
        self, conversation: Conversation
    ) -> tuple[Conversation, tuple[BotMessage, ...]]:
        streak = conversation.unintelligible_streak + 1
        conversation = conversation.model_copy(update={"unintelligible_streak": streak})
        if streak < MAX_REPEAT_REQUESTS:
            return _say(conversation, templates.ask_to_repeat())
        claim = close(conversation.claim, ClaimStatus.ESCALATED, ClosureReason.RETRIES_EXHAUSTED)
        conversation = conversation.model_copy(update={"claim": claim})
        return _say(conversation, templates.closed(claim, policy=None))

    async def _converse(
        self, conversation: Conversation, turn: _Turn
    ) -> tuple[Conversation, tuple[BotMessage, ...]]:
        before = conversation.claim
        try:
            async with asyncio.timeout(self._turn_deadline):
                conversation, llm_reply = await self._run_llm(conversation, turn)
        except (_LLMUnavailableError, TimeoutError) as error:
            if isinstance(error, TimeoutError):
                logger.warning("LLM part of the turn exceeded %.0f s", self._turn_deadline)
                conversation = turn.progress or conversation
            else:
                conversation = error.conversation
            llm_reply = None
            streak = conversation.llm_failure_streak + 1
            conversation = conversation.model_copy(update={"llm_failure_streak": streak})
            if streak >= MAX_LLM_FAILURES and not conversation.claim.status.is_final:
                claim = close(conversation.claim, ClaimStatus.ESCALATED, ClosureReason.SYSTEM_ERROR)
                conversation = conversation.model_copy(update={"claim": claim})
        else:
            conversation = conversation.model_copy(update={"llm_failure_streak": 0})

        after = conversation.claim
        replies: list[BotMessage] = []
        if after.injuries and not before.injuries:
            replies.append(templates.emergency_advice())
        if _needs_script(before, after):
            replies.append(await self._script(after))
            if after.status is ClaimStatus.PENDING_CONFIRMATION:
                conversation = conversation.model_copy(
                    update={"read_back_fingerprint": after.data_fingerprint}
                )
        elif llm_reply is not None:
            self._check_hallucinations(llm_reply, conversation)
            replies.append(BotMessage(llm_reply, scripted=False))
        else:
            replies.append(templates.technical_problem())
        return _say(conversation, *replies)

    async def _script(self, claim: Claim) -> BotMessage:
        if claim.status is ClaimStatus.CONFIRMED:
            assert claim.reference
            return templates.confirmed(claim.reference)
        if claim.status is ClaimStatus.PENDING_CONFIRMATION:
            return templates.read_back(claim)
        return templates.closed(claim, await self._policy_of(claim))

    async def _policy_of(self, claim: Claim) -> Policy | None:
        """The claim's policy, once the declarant is identified by its number and plate."""
        if claim.policy_number is None or claim.plate is None:
            return None
        return identified_policy(claim, await self._repository.get_policy(claim.policy_number))

    # --- LLM loop -------------------------------------------------------------------------

    async def _run_llm(
        self, conversation: Conversation, turn: _Turn
    ) -> tuple[Conversation, str | None]:
        """Let the LLM call tools until it replies in text, or until the claim changes in a way
        a template must announce (then the reply is None). The reply is returned, not added to
        the history. Tool results are kept even if the LLM fails afterwards."""
        before = conversation.claim
        for _ in range(MAX_LLM_ROUNDS):
            completion = await self._complete(conversation, turn.now)
            if not completion.tool_calls:
                if not completion.content or not completion.content.strip():
                    logger.warning("LLM returned neither text nor tool calls")
                    raise _LLMUnavailableError(conversation)
                return conversation, completion.content.strip()

            # Text that accompanies tool calls is dropped: it was never said to the declarant,
            # and the reply comes after the tools have run.
            conversation = conversation.add(
                Message(role=Role.ASSISTANT, tool_calls=completion.tool_calls)
            )
            for call in completion.tool_calls:
                if _needs_script(before, conversation.claim):
                    # The declarant must hear the template before anything else happens to
                    # the claim (e.g. a confirmation of data they never heard read back).
                    result: Mapping[str, Any] = {"error": "not run: the claim changed first"}
                else:
                    conversation, result = await self._run_tool(conversation, call, turn)
                conversation = conversation.add(
                    Message(
                        role=Role.TOOL,
                        tool_call_id=call.id,
                        content=json.dumps(result, ensure_ascii=False),
                    )
                )
            turn.progress = conversation
            if _needs_script(before, conversation.claim):
                return conversation, None

        logger.warning("LLM kept calling tools for %d rounds", MAX_LLM_ROUNDS)
        raise _LLMUnavailableError(conversation)

    async def _complete(self, conversation: Conversation, now: datetime) -> Completion:
        claim = conversation.claim
        system = system_prompt(claim, await self._policy_of(claim), now)
        for attempt, delay in enumerate((*LLM_RETRY_DELAYS, None), start=1):
            try:
                return await self._llm.complete(
                    system=system, messages=conversation.messages, tools=tools.TOOLS
                )
            except LLMError as error:
                logger.warning("LLM call failed (attempt %d): %s", attempt, error)
                if delay is None:
                    raise _LLMUnavailableError(conversation) from error
                await self._sleep(delay)
        raise AssertionError("unreachable")

    # --- tools ----------------------------------------------------------------------------

    async def _run_tool(
        self, conversation: Conversation, call: ToolCall, turn: _Turn
    ) -> tuple[Conversation, Mapping[str, Any]]:
        if conversation.claim.status.is_final:
            return conversation, {"error": "the claim is already closed"}
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            logger.warning("Invalid arguments for tool %s: %r", call.name, call.arguments)
            return conversation, {"error": "arguments must be a JSON object"}

        match call.name:
            case tools.UPDATE_CLAIM:
                return await self._update_claim(conversation, arguments, turn)
            case tools.CONFIRM_CLAIM:
                return await self._confirm_claim(conversation, turn.now)
            case tools.DECLARANT_REFUSES:
                return self._declarant_refuses(conversation, arguments, turn)
            case _:
                logger.warning("LLM called unknown tool %s", call.name)
                return conversation, {"error": f"unknown tool {call.name}"}

    async def _update_claim(
        self, conversation: Conversation, proposed: Mapping[str, Any], turn: _Turn
    ) -> tuple[Conversation, Mapping[str, Any]]:
        claim = conversation.claim
        proposed = tools.stated_values(proposed)
        with self._telemetry.span("validation", input=proposed) as span:
            number = policy_number_to_look_up(claim, proposed)
            policy: Policy | None = await self._repository.get_policy(number) if number else None
            result = apply_update(
                claim, proposed, policy=policy, now=turn.now, already_failed=turn.failed_fields
            )
            turn.failed_fields |= result.rejected.keys()
            outcome = {
                "accepted": list(result.accepted),
                "rejected": dict(result.rejected),
                "status": result.claim.status.value,
                "missing_fields": list(result.claim.missing_fields),
            }
            span.update(output=outcome)
        return conversation.model_copy(update={"claim": result.claim}), outcome

    async def _confirm_claim(
        self, conversation: Conversation, now: datetime
    ) -> tuple[Conversation, Mapping[str, Any]]:
        claim = conversation.claim
        if claim.status is not ClaimStatus.PENDING_CONFIRMATION:
            return conversation, {
                "error": "the claim is not complete yet",
                "missing_fields": list(claim.missing_fields),
            }
        if conversation.read_back_fingerprint != claim.data_fingerprint:
            return conversation, {"error": "the declarant has not heard the current data yet"}
        year = now.astimezone(MADRID).year
        sequence = await self._repository.next_reference_sequence(year)
        claim = confirm(claim, format_reference(year, sequence))
        return conversation.model_copy(update={"claim": claim}), {"status": claim.status.value}

    def _declarant_refuses(
        self, conversation: Conversation, arguments: Mapping[str, Any], turn: _Turn
    ) -> tuple[Conversation, Mapping[str, Any]]:
        name = arguments.get("field")
        if name not in conversation.claim.missing_fields:
            return conversation, {"error": f"{name} is not a missing required field"}
        explain = {
            "instruction": "Explain briefly why this field is needed to process the claim "
            "and ask for it once more."
        }
        if name in turn.refused_fields:
            return conversation, explain
        turn.refused_fields.add(name)
        refusals = conversation.refusals | {name: conversation.refusals.get(name, 0) + 1}
        conversation = conversation.model_copy(update={"refusals": refusals})
        if refusals[name] < MAX_REFUSALS:
            return conversation, explain
        claim = close(conversation.claim, ClaimStatus.INCOMPLETE, ClosureReason.DATA_REFUSED)
        return conversation.model_copy(update={"claim": claim}), {"status": claim.status.value}

    # --- observability --------------------------------------------------------------------

    def _check_hallucinations(self, reply: str, conversation: Conversation) -> None:
        declarant_said = " ".join(
            m.content for m in conversation.messages if m.role is Role.USER and m.content
        )
        findings = check_reply(reply, conversation.claim, declarant_said)
        self._telemetry.score(
            "hallucination_check", 0.0 if findings else 1.0, "; ".join(findings) or None
        )
        if findings:
            logger.warning("Possible hallucination in LLM reply: %s", "; ".join(findings))


def _say(
    conversation: Conversation, *messages: BotMessage
) -> tuple[Conversation, tuple[BotMessage, ...]]:
    """Add what the agent says to the history, and return it to be sent."""
    said = (Message(role=Role.ASSISTANT, content=m.text, scripted=m.scripted) for m in messages)
    return conversation.add(*said), messages
