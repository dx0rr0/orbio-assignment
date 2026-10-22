"""How much may go wrong in a conversation before code closes its claim: utterances nobody
could understand, turns without a usable LLM reply, and fields the declarant refuses to give.

Like `MAX_FAILED_ATTEMPTS` for a field's values, these are business rules, so they are decided
here and not in the turn loop. The counters live on the conversation, which stores them.
"""

from claims_agent.domain.claim import ClaimStatus, ClosureReason
from claims_agent.domain.conversation import Conversation
from claims_agent.domain.lifecycle import close

# Unintelligible utterances in a row before the claim is escalated.
MAX_REPEAT_REQUESTS = 3
# Turns in a row without a usable LLM reply before the claim is escalated.
MAX_LLM_FAILURES = 3
# Times the declarant may refuse a field: the first gets an explanation, the second closes.
MAX_REFUSALS = 2


def record_unintelligible(conversation: Conversation) -> Conversation:
    """One more utterance in a row that could not be understood, in a conversation whose
    claim is still open."""
    streak = conversation.unintelligible_streak + 1
    conversation = conversation.model_copy(update={"unintelligible_streak": streak})
    if streak < MAX_REPEAT_REQUESTS:
        return conversation
    claim = close(conversation.claim, ClaimStatus.ESCALATED, ClosureReason.RETRIES_EXHAUSTED)
    return conversation.model_copy(update={"claim": claim})


def record_understood(conversation: Conversation) -> Conversation:
    """An utterance worth answering: the unintelligible ones are no longer in a row."""
    return conversation.model_copy(update={"unintelligible_streak": 0})


def record_llm_failure(conversation: Conversation) -> Conversation:
    """One more turn in a row without a usable LLM reply. A claim the turn's tools already
    closed stays as it is."""
    streak = conversation.llm_failure_streak + 1
    conversation = conversation.model_copy(update={"llm_failure_streak": streak})
    if streak >= MAX_LLM_FAILURES and not conversation.claim.status.is_final:
        claim = close(conversation.claim, ClaimStatus.ESCALATED, ClosureReason.SYSTEM_ERROR)
        conversation = conversation.model_copy(update={"claim": claim})
    return conversation


def record_llm_reply(conversation: Conversation) -> Conversation:
    """A turn the LLM got through: its failures are no longer in a row."""
    return conversation.model_copy(update={"llm_failure_streak": 0})


def record_refusal(conversation: Conversation, field: str) -> Conversation:
    """The declarant refused to give `field` of a claim still open, once more. The caller
    counts one per declarant answer: the LLM may report one refusal several times."""
    refusals = conversation.refusals | {field: conversation.refusals.get(field, 0) + 1}
    conversation = conversation.model_copy(update={"refusals": refusals})
    if refusals[field] < MAX_REFUSALS:
        return conversation
    claim = close(conversation.claim, ClaimStatus.INCOMPLETE, ClosureReason.DATA_REFUSED)
    return conversation.model_copy(update={"claim": claim})
