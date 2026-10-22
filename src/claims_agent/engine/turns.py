"""The turn loop: the LLM converses and proposes values through tools; code validates them,
moves the claim between statuses and writes every critical message (ADR-0002).

Each turn loads the conversation, processes the declarant's utterance and saves it, so any
server process can handle any turn. An utterance with nothing to understand is answered
without the LLM. Otherwise the LLM runs its tools (`tool_loop`, `tool_handlers`), and the
reply is a template if the claim changed in a way only code may announce (`scripts`), else
the LLM's own, else an apology. The limits on what may go wrong live in `domain.limits`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from claims_agent.domain import (
    MADRID,
    ClaimStatus,
    Conversation,
    Message,
    Role,
    record_llm_failure,
    record_llm_reply,
    record_understood,
    record_unintelligible,
)
from claims_agent.engine import templates
from claims_agent.engine.hallucinations import report_reply
from claims_agent.engine.scripts import needs_script, script
from claims_agent.engine.templates import BotMessage
from claims_agent.engine.tool_handlers import ToolHandlers, Turn
from claims_agent.engine.tool_loop import LLMUnavailableError, ToolLoop
from claims_agent.ports import (
    ChatModel,
    ConcurrentUpdateError,
    ConversationRepository,
    NullTelemetry,
    PolicyCatalog,
    ReferenceSequence,
    Telemetry,
)

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


def is_unintelligible(utterance: str) -> bool:
    """Nothing worth sending to the LLM: empty, noise, or punctuation only."""
    return _WORD.search(utterance) is None


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
        self._conversations = conversations
        self._policies = policies
        self._telemetry = telemetry or NullTelemetry()
        self._clock = clock
        handlers = ToolHandlers(policies=policies, references=references, telemetry=self._telemetry)
        self._tool_loop = ToolLoop(
            llm=llm, handlers=handlers, policies=policies, deadline=turn_deadline
        )

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
            conversation, replies = await self._converse(conversation, Turn(now))

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
        self, conversation: Conversation, turn: Turn
    ) -> tuple[Conversation, tuple[BotMessage, ...]]:
        before = conversation.claim
        try:
            conversation, llm_reply = await self._tool_loop.run(conversation, turn)
        except LLMUnavailableError as error:
            conversation, llm_reply = record_llm_failure(error.conversation), None
        else:
            conversation = record_llm_reply(conversation)

        after = conversation.claim
        replies: list[BotMessage] = []
        if after.injuries and not before.injuries:
            replies.append(templates.emergency_advice())
        if needs_script(before, after):
            heard = conversation.read_back_claim
            replies.append(await script(after, heard=heard, policies=self._policies))
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


def _say(
    conversation: Conversation, *messages: BotMessage
) -> tuple[Conversation, tuple[BotMessage, ...]]:
    """Add what the agent says to the history, and return it to be sent."""
    said = (Message(role=Role.ASSISTANT, content=m.text, scripted=m.scripted) for m in messages)
    return conversation.add(*said), messages
