from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync
from django.utils import timezone

from claims_agent.domain import (
    Claim,
    ClaimStatus,
    ClosureReason,
    Conversation,
    PendingField,
)
from claims_agent.engine.summary import RECORD_KEY_POINTS
from claims_service.intake import services, tasks
from claims_service.intake.repository import DjangoConversationRepository
from claims_service.intake.tasks import (
    ABANDONED_AFTER,
    SUMMARY_GIVE_UP_AFTER,
    SUMMARY_GRACE,
    generate_claim_summary,
    queue_missing_summaries,
    sweep_abandoned_conversations,
)
from tests.engine.fakes import call

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def notified(monkeypatch) -> list[Conversation]:
    sent: list[Conversation] = []
    monkeypatch.setattr(tasks, "_notify", sent.append)
    return sent


def store(repository, *, idle: timedelta, claim: Claim | None = None) -> Conversation:
    last_activity = timezone.now() - idle
    conversation = Conversation(
        claim=claim or Claim(declarant_name="Ana García"),
        created_at=last_activity,
        updated_at=last_activity,
    )
    return async_to_sync(repository.create)(conversation)


# --- abandonment sweep --------------------------------------------------------------------


def test_idle_conversations_are_closed_as_abandoned(repository, notified):
    idle = store(repository, idle=ABANDONED_AFTER + timedelta(minutes=1))

    assert sweep_abandoned_conversations() == 1

    claim = async_to_sync(repository.get)(idle.id).claim
    assert claim.status is ClaimStatus.INCOMPLETE
    assert claim.closure_reason is ClosureReason.ABANDONED
    assert PendingField(field="policy_number") in claim.pending_fields
    assert claim.declarant_name == "Ana García"  # partial data is kept
    assert [c.id for c in notified] == [idle.id]


def test_recent_and_closed_conversations_are_left_alone(repository, notified):
    recent = store(repository, idle=ABANDONED_AFTER - timedelta(minutes=1))
    confirmed = store(
        repository,
        idle=timedelta(hours=1),
        claim=Claim(status=ClaimStatus.CONFIRMED, reference="SIN-2026-000001"),
    )

    assert sweep_abandoned_conversations() == 0

    assert async_to_sync(repository.get)(recent.id).claim.status is ClaimStatus.IN_PROGRESS
    assert async_to_sync(repository.get)(confirmed.id).claim.status is ClaimStatus.CONFIRMED
    assert notified == []


def test_a_turn_saved_during_the_sweep_wins(repository, notified, monkeypatch):
    idle = store(repository, idle=ABANDONED_AFTER + timedelta(minutes=1))
    original_save = DjangoConversationRepository.save

    async def turn_lands_first(self, conversation):
        # The declarant speaks between the sweep's read and its conditional write.
        await original_save(self, await self.get(conversation.id))
        return await original_save(self, conversation)

    monkeypatch.setattr(DjangoConversationRepository, "save", turn_lands_first)

    assert sweep_abandoned_conversations() == 0
    assert async_to_sync(repository.get)(idle.id).claim.status is ClaimStatus.IN_PROGRESS
    assert notified == []


# --- summary ------------------------------------------------------------------------------


@pytest.fixture
def summary_llm(monkeypatch, llm):
    monkeypatch.setattr(services, "chat_model", lambda: llm)
    return llm


def confirmed_claim() -> Claim:
    return Claim(
        status=ClaimStatus.CONFIRMED,
        reference="SIN-2026-000001",
        loss_type="broken_glass",
        injuries=False,
        vehicle_immobilized=True,
    )


def test_summary_is_stored_and_pushed(repository, notified, summary_llm):
    conversation = store(repository, idle=timedelta(0), claim=confirmed_claim())
    summary_llm.then(call(RECORD_KEY_POINTS, {"key_points": ["Rotura del parabrisas en la A-3."]}))

    generate_claim_summary(str(conversation.id))

    summary = async_to_sync(repository.get)(conversation.id).summary
    assert summary is not None
    assert summary.key_points == ("Rotura del parabrisas en la A-3.",)
    assert summary.urgency == "medium"
    assert "Ofrecer grúa y taller concertado." in summary.next_steps
    assert [c.summary for c in notified] == [summary]
    # The LLM saw the claim data and was given only the summary tool.
    [request] = summary_llm.requests
    assert '"loss_type": "broken_glass"' in request["messages"][0].content
    assert [t["function"]["name"] for t in request["tools"]] == [RECORD_KEY_POINTS]


def test_summary_is_not_regenerated(repository, notified, summary_llm):
    conversation = store(repository, idle=timedelta(0), claim=confirmed_claim())
    summary_llm.then(call(RECORD_KEY_POINTS, {"key_points": ["Primero."]}))
    generate_claim_summary(str(conversation.id))

    generate_claim_summary(str(conversation.id))  # e.g. a redelivered task

    assert len(summary_llm.requests) == 1
    assert len(notified) == 1


def test_unconfirmed_claims_get_no_summary(repository, notified, summary_llm):
    conversation = store(repository, idle=timedelta(0))

    generate_claim_summary(str(conversation.id))

    assert summary_llm.requests == []
    assert async_to_sync(repository.get)(conversation.id).summary is None


def test_confirmed_claims_whose_summary_was_never_queued_are_queued(repository, monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(tasks.generate_claim_summary, "delay", queued.append)
    lost = store(repository, idle=SUMMARY_GRACE + timedelta(minutes=1), claim=confirmed_claim())
    store(repository, idle=timedelta(0), claim=confirmed_claim())  # its task may be in flight
    store(repository, idle=SUMMARY_GIVE_UP_AFTER + timedelta(hours=1), claim=confirmed_claim())
    store(repository, idle=timedelta(hours=1))  # not confirmed
    summarized = Conversation(
        claim=confirmed_claim(),
        summary={"key_points": ["Hecho."], "urgency": "medium", "next_steps": []},
        created_at=timezone.now() - timedelta(hours=1),
        updated_at=timezone.now() - timedelta(hours=1),
    )
    async_to_sync(repository.create)(summarized)

    assert queue_missing_summaries() == 1

    assert queued == [str(lost.id)]
