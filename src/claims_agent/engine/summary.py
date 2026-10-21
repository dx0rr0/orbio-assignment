"""Summary of a confirmed claim for the adjuster: key points written by the LLM from the
claim and the transcript; urgency and next steps decided by code."""

import json
from collections.abc import Sequence

from claims_agent.domain import ClaimSummary, Conversation, Message, Role, ToolCall
from claims_agent.domain.next_steps import next_steps
from claims_agent.ports import ChatModel, LLMError, ToolSpec

RECORD_KEY_POINTS = "record_key_points"
_TOOL: ToolSpec = {
    "type": "function",
    "function": {
        "name": RECORD_KEY_POINTS,
        "description": "Record the key points of the claim for the adjuster.",
        "parameters": {
            "type": "object",
            "properties": {
                "key_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                }
            },
            "required": ["key_points"],
            "additionalProperties": False,
        },
    },
}
_SYSTEM = (
    "You write claim summaries for car insurance adjusters. From the claim data and the "
    "conversation, write 3 to 6 key points in Spanish, one short sentence each: what "
    "happened, who was involved, damage and injuries, and anything the declarant said that "
    f"an adjuster should know. Use only facts present in the input. Call {RECORD_KEY_POINTS}."
)


class SummaryError(RuntimeError):
    pass


async def summarize(llm: ChatModel, conversation: Conversation) -> ClaimSummary:
    claim = conversation.claim
    transcript = "\n".join(
        f"{'Declarante' if m.role is Role.USER else 'Agente'}: {m.content}"
        for m in conversation.transcript
    )
    data = claim.model_dump(mode="json", include=set(claim.required_fields))
    prompt = (
        f"Claim data:\n{json.dumps(data, ensure_ascii=False, indent=2)}\n\n"
        f"Conversation:\n{transcript}"
    )
    try:
        completion = await llm.complete(
            system=_SYSTEM, messages=[Message(role=Role.USER, content=prompt)], tools=[_TOOL]
        )
    except LLMError as error:
        raise SummaryError(str(error)) from error

    key_points = _key_points(completion.tool_calls)
    assert claim.urgency is not None, "a confirmed claim has all the data urgency needs"
    return ClaimSummary(key_points=key_points, urgency=claim.urgency, next_steps=next_steps(claim))


def _key_points(tool_calls: Sequence[ToolCall]) -> tuple[str, ...]:
    call = next((c for c in tool_calls if c.name == RECORD_KEY_POINTS), None)
    if call is not None:
        try:
            points = json.loads(call.arguments).get("key_points")
        except (json.JSONDecodeError, AttributeError):
            points = None
        if isinstance(points, list) and all(isinstance(p, str) for p in points):
            cleaned = tuple(p.strip() for p in points if p.strip())
            if cleaned:
                return cleaned
    raise SummaryError("the LLM did not record valid key points")
