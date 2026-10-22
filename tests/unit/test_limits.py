from functools import partial

from claims_agent.domain import (
    MAX_LLM_FAILURES,
    MAX_REFUSALS,
    MAX_REPEAT_REQUESTS,
    ClaimStatus,
    ClosureReason,
    Conversation,
    PendingField,
    close,
    record_llm_failure,
    record_llm_reply,
    record_refusal,
    record_understood,
    record_unintelligible,
)
from tests.support import NOW


def repeated(record, conversation: Conversation, times: int) -> Conversation:
    for _ in range(times):
        conversation = record(conversation)
    return conversation


unheard = partial(record_unintelligible, transcribed=True)
untranscribed = partial(record_unintelligible, transcribed=False)


def test_unintelligible_utterances_in_a_row_escalate_the_claim():
    almost = repeated(unheard, Conversation.start(NOW), MAX_REPEAT_REQUESTS - 1)
    assert almost.claim.status is ClaimStatus.IN_PROGRESS

    claim = unheard(almost).claim

    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.RETRIES_EXHAUSTED
    assert PendingField(field="policy_number") in claim.pending_fields


def test_failed_transcriptions_in_a_row_escalate_as_a_system_error():
    almost = repeated(untranscribed, Conversation.start(NOW), MAX_REPEAT_REQUESTS - 1)

    claim = untranscribed(almost).claim

    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.SYSTEM_ERROR


def test_a_row_with_noise_among_failed_transcriptions_escalates_as_bad_data():
    almost = repeated(untranscribed, Conversation.start(NOW), MAX_REPEAT_REQUESTS - 1)

    claim = unheard(almost).claim

    assert claim.closure_reason is ClosureReason.RETRIES_EXHAUSTED


def test_an_understood_utterance_breaks_the_row():
    almost = repeated(untranscribed, Conversation.start(NOW), MAX_REPEAT_REQUESTS - 1)

    conversation = untranscribed(record_understood(almost))

    assert conversation.unintelligible_streak == 1
    assert conversation.transcription_failure_streak == 1
    assert conversation.claim.status is ClaimStatus.IN_PROGRESS


def test_turns_without_an_llm_reply_in_a_row_escalate_as_a_system_error():
    almost = repeated(record_llm_failure, Conversation.start(NOW), MAX_LLM_FAILURES - 1)
    assert almost.claim.status is ClaimStatus.IN_PROGRESS

    claim = record_llm_failure(almost).claim

    assert claim.status is ClaimStatus.ESCALATED
    assert claim.closure_reason is ClosureReason.SYSTEM_ERROR


def test_an_llm_reply_breaks_the_row():
    almost = repeated(record_llm_failure, Conversation.start(NOW), MAX_LLM_FAILURES - 1)

    conversation = record_llm_failure(record_llm_reply(almost))

    assert conversation.llm_failure_streak == 1
    assert conversation.claim.status is ClaimStatus.IN_PROGRESS


def test_a_failed_llm_turn_leaves_a_claim_its_tools_closed_as_it_is():
    almost = repeated(record_llm_failure, Conversation.start(NOW), MAX_LLM_FAILURES - 1)
    refused = close(almost.claim, ClaimStatus.INCOMPLETE, ClosureReason.DATA_REFUSED)

    conversation = record_llm_failure(almost.model_copy(update={"claim": refused}))

    assert conversation.claim == refused
    assert conversation.llm_failure_streak == MAX_LLM_FAILURES


def test_refusing_a_field_again_closes_the_claim_as_incomplete():
    conversation = Conversation.start(NOW)
    for _ in range(MAX_REFUSALS - 1):
        conversation = record_refusal(conversation, "declarant_name")
    assert conversation.claim.status is ClaimStatus.IN_PROGRESS

    conversation = record_refusal(conversation, "declarant_name")

    assert conversation.refusals == {"declarant_name": MAX_REFUSALS}
    assert conversation.claim.status is ClaimStatus.INCOMPLETE
    assert conversation.claim.closure_reason is ClosureReason.DATA_REFUSED


def test_refusals_are_counted_per_field():
    conversation = Conversation.start(NOW)
    for field in ("declarant_name", "plate"):
        conversation = record_refusal(conversation, field)

    assert conversation.refusals == {"declarant_name": 1, "plate": 1}
    assert conversation.claim.status is ClaimStatus.IN_PROGRESS
