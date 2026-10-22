"""The turn loop: the LLM converses and proposes values through tools; code validates them,
moves the claim between statuses and writes every critical message (ADR-0002).

Each turn loads the conversation, processes the declarant's utterance and saves it, so any
server process can handle any turn.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from claims_agent.domain import (
    MADRID,
    Claim,
    ClaimStatus,
    Conversation,
    Message,
    Policy,
    Role,
    ToolCall,
    apply_update,
    confirm,
    confirm_value,
    format_reference,
    identified_policy,
    policy_number_to_look_up,
    record_llm_failure,
    record_llm_reply,
    record_refusal,
    record_understood,
    record_unintelligible,
)
from claims_agent.engine import templates, tools
from claims_agent.engine.hallucinations import report_reply
from claims_agent.engine.prompts import system_prompt
from claims_agent.engine.templates import BotMessage
from claims_agent.engine.tools import ToolResult
from claims_agent.ports import (
    ChatModel,
    Completion,
    ConcurrentUpdateError,
    ConversationRepository,
    LLMError,
    NullTelemetry,
    PolicyCatalog,
    ReferenceSequence,
    Telemetry,
)

logger = logging.getLogger(__name__)

# LLM calls per turn: tool rounds plus the final reply.
MAX_LLM_ROUNDS = 4
# Seconds the LLM part of a turn may take, retries included, before the agent apologises.
TURN_DEADLINE_SECONDS = 25.0

_WORD = re.compile(r"\w")


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
    if after.coverage_issue is not None:
        return before.fingerprint_to_confirm != after.fingerprint_to_confirm
    return after.status is ClaimStatus.PENDING_CONFIRMATION and (
        before.status is not ClaimStatus.PENDING_CONFIRMATION
        or before.data_fingerprint != after.data_fingerprint
    )


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


class ConversationEngine:
    """`llm` is called once per completion the turn needs: wrap it in a `RetryingChatModel` for
    transient errors to be retried."""

    def __init__(
        self,
        *,
        llm: ChatModel,
        conversations: ConversationRepository,
        policies: PolicyCatalog,
        references: ReferenceSequence,
        telemetry: Telemetry | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(MADRID),
        turn_deadline: float = TURN_DEADLINE_SECONDS,
    ) -> None:
        self._llm = llm
        self._conversations = conversations
        self._policies = policies
        self._references = references
        self._telemetry = telemetry or NullTelemetry()
        self._clock = clock
        self._turn_deadline = turn_deadline

    async def start(self) -> TurnResult:
        conversation, replies = _say(Conversation.start(self._clock()), templates.greeting())
        return TurnResult(await self._conversations.create(conversation), replies)

    async def handle_text(self, conversation_id: UUID, utterance: str) -> TurnResult:
        now = self._clock()
        conversation = await self._conversations.get(conversation_id)
        if not conversation.claim.status.is_final:
            # Mark the activity before the slow part of the turn, so that the abandonment
            # sweep leaves the conversation alone while the turn is being processed.
            try:
                conversation = await self._conversations.save(
                    conversation.model_copy(update={"updated_at": now})
                )
            except ConcurrentUpdateError:
                # Saved meanwhile, e.g. closed by the sweep right after we loaded it.
                conversation = await self._conversations.get(conversation_id)
        if conversation.claim.status.is_final:
            status = conversation.claim.status
            reply = templates.already_closed(conversation.claim)
            return TurnResult(conversation, (reply,), previous_status=status)

        previous_status = conversation.claim.status
        if is_unintelligible(utterance):
            conversation, replies = self._unintelligible(conversation)
        else:
            conversation = conversation.add(Message(role=Role.USER, content=utterance.strip()))
            conversation = record_understood(conversation)
            conversation, replies = await self._converse(conversation, _Turn(now))

        conversation = conversation.model_copy(update={"updated_at": now})
        try:
            saved = await self._conversations.save(conversation)
        except ConcurrentUpdateError:
            stored = await self._conversations.get(conversation_id)
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
        conversation = record_unintelligible(conversation)
        if not conversation.claim.status.is_final:
            return _say(conversation, templates.ask_to_repeat())
        return _say(conversation, templates.closed(conversation.claim, policy=None))

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
            conversation = record_llm_failure(conversation)
        else:
            conversation = record_llm_reply(conversation)

        after = conversation.claim
        replies: list[BotMessage] = []
        if after.injuries and not before.injuries:
            replies.append(templates.emergency_advice())
        if _needs_script(before, after):
            replies.append(await self._script(after, heard=conversation.read_back_claim))
            if not after.status.is_final:
                conversation = conversation.model_copy(
                    update={"read_back_fingerprint": after.fingerprint_to_confirm}
                )
            if after.status is ClaimStatus.PENDING_CONFIRMATION:
                conversation = conversation.model_copy(update={"read_back_claim": after})
        elif llm_reply is not None:
            report_reply(llm_reply, conversation, self._telemetry)
            replies.append(BotMessage(llm_reply, scripted=False))
        else:
            replies.append(templates.technical_problem())
        return _say(conversation, *replies)

    async def _script(self, claim: Claim, *, heard: Claim | None) -> BotMessage:
        """`heard` is the claim as last read back to the declarant, if it was."""
        if claim.status is ClaimStatus.CONFIRMED:
            if claim.reference is None:
                raise ValueError("a confirmed claim always has a reference")
            return templates.confirmed(claim.reference)
        if claim.status is ClaimStatus.PENDING_CONFIRMATION:
            if heard is None:
                return templates.read_back(claim)
            return templates.read_back_changes(heard, claim)
        if not claim.status.is_final:
            return templates.confirm_coverage_value(claim, await self._policy_of(claim))
        return templates.closed(claim, await self._policy_of(claim))

    async def _policy_of(self, claim: Claim) -> Policy | None:
        """The claim's policy, once the declarant is identified by its number and plate."""
        if claim.policy_number is None or claim.plate is None:
            return None
        return identified_policy(claim, await self._policies.get_policy(claim.policy_number))

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
            results: list[ToolResult] = []
            for call in completion.tool_calls:
                if _needs_script(before, conversation.claim):
                    # The declarant must hear the template before anything else happens to
                    # the claim (e.g. a confirmation of data they never heard read back).
                    result = ToolResult(error="not run: the claim changed first")
                else:
                    conversation, result = await self._run_tool(conversation, call, turn)
                results.append(result)
                conversation = conversation.add(
                    Message(
                        role=Role.TOOL,
                        tool_call_id=call.id,
                        content=json.dumps(result.as_json(), ensure_ascii=False),
                    )
                )
            turn.progress = conversation
            if _needs_script(before, conversation.claim):
                return conversation, None
            if (prepared := _prepared_reply(completion.tool_calls, results)) is not None:
                return conversation, prepared

        logger.warning("LLM kept calling tools for %d rounds", MAX_LLM_ROUNDS)
        raise _LLMUnavailableError(conversation)

    async def _complete(self, conversation: Conversation, now: datetime) -> Completion:
        claim = conversation.claim
        system = system_prompt(claim, await self._policy_of(claim), now)
        try:
            return await self._llm.complete(
                system=system, messages=conversation.messages, tools=tools.TOOLS
            )
        except LLMError as error:
            raise _LLMUnavailableError(conversation) from error

    # --- tools ----------------------------------------------------------------------------

    async def _run_tool(
        self, conversation: Conversation, call: ToolCall, turn: _Turn
    ) -> tuple[Conversation, ToolResult]:
        if conversation.claim.status.is_final:
            return conversation, ToolResult(error="the claim is already closed")
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            logger.warning("Invalid arguments for tool %s: %r", call.name, call.arguments)
            return conversation, ToolResult(error="arguments must be a JSON object")

        match call.name:
            case tools.UPDATE_CLAIM:
                return await self._update_claim(conversation, arguments, turn)
            case tools.CONFIRM_CLAIM:
                return await self._confirm_claim(conversation, turn.now)
            case tools.CONFIRM_VALUE:
                return self._confirm_value(conversation)
            case tools.DECLARANT_REFUSES:
                return self._declarant_refuses(conversation, arguments, turn)
            case _:
                logger.warning("LLM called unknown tool %s", call.name)
                return conversation, ToolResult(error=f"unknown tool {call.name}")

    async def _update_claim(
        self, conversation: Conversation, proposed: Mapping[str, JsonValue], turn: _Turn
    ) -> tuple[Conversation, ToolResult]:
        claim = conversation.claim
        proposed = tools.stated_values(
            {name: value for name, value in proposed.items() if name != tools.REPLY_IF_ACCEPTED}
        )
        with self._telemetry.span("validation", input=proposed) as span:
            number = policy_number_to_look_up(claim, proposed)
            policy: Policy | None = await self._policies.get_policy(number) if number else None
            result = apply_update(
                claim, proposed, policy=policy, now=turn.now, already_failed=turn.failed_fields
            )
            turn.failed_fields |= result.rejected.keys()
            outcome = ToolResult(
                accepted=result.accepted,
                rejected=dict(result.rejected),
                status=result.claim.status,
                missing_fields=result.claim.missing_fields,
            )
            span.update(output=outcome.as_json())
        return conversation.model_copy(update={"claim": result.claim}), outcome

    async def _confirm_claim(
        self, conversation: Conversation, now: datetime
    ) -> tuple[Conversation, ToolResult]:
        claim = conversation.claim
        if claim.status is not ClaimStatus.PENDING_CONFIRMATION:
            return conversation, ToolResult(
                error="the claim is not complete yet", missing_fields=claim.missing_fields
            )
        if conversation.read_back_fingerprint != claim.data_fingerprint:
            return conversation, ToolResult(
                error="the declarant has not heard the current data yet"
            )
        year = now.astimezone(MADRID).year
        sequence = await self._references.next_reference_sequence(year)
        claim = confirm(claim, format_reference(year, sequence))
        return conversation.model_copy(update={"claim": claim}), ToolResult(status=claim.status)

    def _confirm_value(self, conversation: Conversation) -> tuple[Conversation, ToolResult]:
        claim = conversation.claim
        if claim.coverage_issue is None:
            return conversation, ToolResult(error="no value is waiting for confirmation")
        if conversation.read_back_fingerprint != claim.fingerprint_to_confirm:
            return conversation, ToolResult(error="the declarant has not been asked about it yet")
        claim = confirm_value(claim)
        return conversation.model_copy(update={"claim": claim}), ToolResult(status=claim.status)

    def _declarant_refuses(
        self, conversation: Conversation, arguments: Mapping[str, JsonValue], turn: _Turn
    ) -> tuple[Conversation, ToolResult]:
        name = arguments.get("field")
        if not isinstance(name, str) or name not in conversation.claim.missing_fields:
            return conversation, ToolResult(error=f"{name} is not a missing required field")
        explain = ToolResult(
            instruction="Explain briefly why this field is needed to process the claim "
            "and ask for it once more."
        )
        if name in turn.refused_fields:
            return conversation, explain
        turn.refused_fields.add(name)
        conversation = record_refusal(conversation, name)
        if not conversation.claim.status.is_final:
            return conversation, explain
        return conversation, ToolResult(status=conversation.claim.status)


def _say(
    conversation: Conversation, *messages: BotMessage
) -> tuple[Conversation, tuple[BotMessage, ...]]:
    """Add what the agent says to the history, and return it to be sent."""
    said = (Message(role=Role.ASSISTANT, content=m.text, scripted=m.scripted) for m in messages)
    return conversation.add(*said), messages
