from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from django.db import connection
from django.utils import timezone

from claims_agent.domain import Claim, Conversation
from claims_agent.ports import ConcurrentUpdateError, ConversationNotFoundError
from claims_service.intake.models import ConversationRecord
from claims_service.intake.repository import _next_sequence

pytestmark = pytest.mark.django_db(transaction=True)


def new_conversation() -> Conversation:
    return Conversation.start(timezone.now())


async def test_created_conversation_reads_back_equal(repository):
    conversation = new_conversation().model_copy(
        update={"claim": Claim(declarant_name="Ana García"), "refusals": {"plate": 1}}
    )

    await repository.create(conversation)

    assert await repository.get(conversation.id) == conversation


async def test_status_and_activity_are_copied_into_columns(repository):
    conversation = await repository.create(new_conversation())

    record = await ConversationRecord.objects.aget(id=conversation.id)

    assert record.status == "in_progress"
    assert record.updated_at == conversation.updated_at


async def test_save_bumps_the_version(repository):
    conversation = await repository.create(new_conversation())

    saved = await repository.save(conversation)

    assert saved.version == conversation.version + 1
    assert (await repository.get(conversation.id)).version == saved.version


async def test_saving_a_stale_copy_is_refused(repository):
    conversation = await repository.create(new_conversation())
    await repository.save(conversation.model_copy(update={"claim": Claim(declarant_name="Ana")}))

    with pytest.raises(ConcurrentUpdateError):
        await repository.save(conversation.model_copy(update={"claim": Claim(plate="1234BCD")}))

    assert (await repository.get(conversation.id)).claim.declarant_name == "Ana"


async def test_missing_conversation(repository):
    missing = new_conversation()
    with pytest.raises(ConversationNotFoundError):
        await repository.get(missing.id)
    with pytest.raises(ConversationNotFoundError):
        await repository.save(missing)


async def test_policy_lookup(repository, policies):
    policy = await repository.get_policy("AUT-48213907")

    assert policy is not None
    assert (policy.holder_name, policy.plate) == ("María López Fernández", "4721KLM")
    assert isinstance(policy.start_date, date)
    assert await repository.get_policy("AUT-00000000") is None


async def test_reference_sequence_counts_per_year(repository):
    assert [await repository.next_reference_sequence(2026) for _ in range(3)] == [1, 2, 3]
    assert await repository.next_reference_sequence(2027) == 1


def test_reference_sequence_is_unique_across_concurrent_connections():
    def next_number(_: int) -> int:
        try:  # each thread has its own database connection
            return _next_sequence(2026)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(next_number, range(40)))

    assert sorted(numbers) == list(range(1, 41))
