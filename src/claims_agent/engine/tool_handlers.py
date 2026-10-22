"""What each tool call the LLM makes does to the conversation. The LLM only proposes; these
handlers validate through the domain and tell the LLM the outcome (ADR-0002)."""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import JsonValue

from claims_agent.domain import (
    MADRID,
    ClaimStatus,
    Conversation,
    Policy,
    ToolCall,
    apply_update,
    confirm,
    confirm_value,
    format_reference,
    policy_number_to_look_up,
    record_refusal,
)
from claims_agent.engine import tools
from claims_agent.engine.tools import ToolResult
from claims_agent.ports import PolicyCatalog, ReferenceSequence, Telemetry

logger = logging.getLogger(__name__)


@dataclass
class Turn:
    """What the tool calls of one declarant turn share: when it is, and what it has counted so
    far, so that a value the LLM sends again within the turn counts as one answer (one failed
    attempt, one refusal)."""

    now: datetime
    failed_fields: set[str] = field(default_factory=set)
    refused_fields: set[str] = field(default_factory=set)


# Every handler takes the conversation, the call's arguments and the turn, and returns the
# conversation after the call and what the LLM is told.
type Handled = tuple[Conversation, ToolResult]
type ToolHandler = Callable[[Conversation, Mapping[str, JsonValue], Turn], Awaitable[Handled]]


class ToolHandlers:
    def __init__(
        self, *, policies: PolicyCatalog, references: ReferenceSequence, telemetry: Telemetry
    ) -> None:
        self._policies = policies
        self._references = references
        self._telemetry = telemetry
        self._handlers: dict[str, ToolHandler] = {
            tools.UPDATE_CLAIM: self._update_claim,
            tools.CONFIRM_CLAIM: self._confirm_claim,
            tools.CONFIRM_VALUE: self._confirm_value,
            tools.DECLARANT_REFUSES: self._declarant_refuses,
        }

    async def run(self, conversation: Conversation, call: ToolCall, turn: Turn) -> Handled:
        """Run `call` on `conversation`. Anything wrong with the call itself (a closed claim,
        arguments that are not a JSON object, an unknown tool) is an error for the LLM."""
        if conversation.claim.status.is_final:
            return conversation, ToolResult(error="the claim is already closed")
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            logger.warning("Invalid arguments for tool %s: %r", call.name, call.arguments)
            return conversation, ToolResult(error="arguments must be a JSON object")
        handler = self._handlers.get(call.name)
        if handler is None:
            logger.warning("LLM called unknown tool %s", call.name)
            return conversation, ToolResult(error=f"unknown tool {call.name}")
        return await handler(conversation, arguments, turn)

    async def _update_claim(
        self, conversation: Conversation, proposed: Mapping[str, JsonValue], turn: Turn
    ) -> Handled:
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
        self, conversation: Conversation, arguments: Mapping[str, JsonValue], turn: Turn
    ) -> Handled:
        claim = conversation.claim
        if claim.status is not ClaimStatus.PENDING_CONFIRMATION:
            return conversation, ToolResult(
                error="the claim is not complete yet", missing_fields=claim.missing_fields
            )
        if conversation.read_back_fingerprint != claim.data_fingerprint:
            return conversation, ToolResult(
                error="the declarant has not heard the current data yet"
            )
        year = turn.now.astimezone(MADRID).year
        sequence = await self._references.next_reference_sequence(year)
        claim = confirm(claim, format_reference(year, sequence))
        return conversation.model_copy(update={"claim": claim}), ToolResult(status=claim.status)

    async def _confirm_value(
        self, conversation: Conversation, arguments: Mapping[str, JsonValue], turn: Turn
    ) -> Handled:
        claim = conversation.claim
        if claim.coverage_issue is None:
            return conversation, ToolResult(error="no value is waiting for confirmation")
        if conversation.read_back_fingerprint != claim.fingerprint_to_confirm:
            return conversation, ToolResult(error="the declarant has not been asked about it yet")
        claim = confirm_value(claim)
        return conversation.model_copy(update={"claim": claim}), ToolResult(status=claim.status)

    async def _declarant_refuses(
        self, conversation: Conversation, arguments: Mapping[str, JsonValue], turn: Turn
    ) -> Handled:
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
