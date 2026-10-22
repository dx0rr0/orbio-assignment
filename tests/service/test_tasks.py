from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.utils import timezone

from claims_agent.domain import (
    Claim,
    ClaimStatus,
    ClosureReason,
    Conversation,
    PendingField,
    apply_update,
    confirm,
)
from claims_agent.engine import ConversationEngine, templates
from claims_agent.engine.summary import RECORD_KEY_POINTS
from claims_agent.ports import NullTelemetry
from claims_service.intake import tasks
from claims_service.intake.repository import DjangoConversationRepository
from claims_service.intake.tasks import (
    ABANDONED_AFTER,
    SUMMARY_GIVE_UP_AFTER,
    SUMMARY_GRACE,
    TaskServices,
    generate_claim_summary,
    queue_missing_summaries,
    sweep_abandoned_conversations,
)
from tests.engine.fakes import LLM_DOWN, call, no_sleep, say
from tests.support import COMPLETE, HAPPY_POLICY, NOW

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def notified() -> list[Conversation]:
    return []


@pytest.fixture
def queued() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def task_services(repository, llm, notified, queued) -> TaskServices:
    services = TaskServices(
        repository=repository,
        telemetry=NullTelemetry(),
        open_chat_model=lambda: nullcontext(llm),
        notify=notified.append,
        queue_summary=queued.append,
    )
    tasks.configure(services)
    return services


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


class TurnLandsFirst(DjangoConversationRepository):
    async def save(self, conversation):
        # The declarant speaks between the sweep's read and its conditional write.
        await super().save(await self.get(conversation.id))
        return await super().save(conversation)


def test_a_turn_saved_during_the_sweep_wins(repository, notified, task_services):
    idle = store(repository, idle=ABANDONED_AFTER + timedelta(minutes=1))
    tasks.configure(replace(task_services, repository=TurnLandsFirst()))

    assert sweep_abandoned_conversations() == 0
    assert async_to_sync(repository.get)(idle.id).claim.status is ClaimStatus.IN_PROGRESS
    assert notified == []


# --- summary ------------------------------------------------------------------------------


def confirmed_claim() -> Claim:
    """The happy path's broken glass, but with a vehicle that cannot be driven."""
    proposed = COMPLETE | {"vehicle_immobilized": True}
    claim = apply_update(Claim(), proposed, policy=HAPPY_POLICY, now=NOW).claim
    return confirm(claim, "SIN-2026-000001")


def test_summary_is_stored_and_pushed(repository, notified, llm):
    conversation = store(repository, idle=timedelta(0), claim=confirmed_claim())
    llm.then(call(RECORD_KEY_POINTS, {"key_points": ["Rotura del parabrisas en la A-3."]}))

    generate_claim_summary(str(conversation.id))

    summary = async_to_sync(repository.get)(conversation.id).summary
    assert summary is not None
    assert summary.key_points == ("Rotura del parabrisas en la A-3.",)
    assert summary.urgency == "medium"
    assert "Ofrecer grúa y taller concertado." in summary.next_steps
    assert [c.summary for c in notified] == [summary]
    # The LLM saw the claim data and was given only the summary tool.
    [request] = llm.requests
    assert '"loss_type": "broken_glass"' in request["messages"][0].content
    assert [t["function"]["name"] for t in request["tools"]] == [RECORD_KEY_POINTS]


def test_summary_is_not_regenerated(repository, notified, llm):
    conversation = store(repository, idle=timedelta(0), claim=confirmed_claim())
    llm.then(call(RECORD_KEY_POINTS, {"key_points": ["Primero."]}))
    generate_claim_summary(str(conversation.id))

    generate_claim_summary(str(conversation.id))  # e.g. a redelivered task

    assert len(llm.requests) == 1
    assert len(notified) == 1


def test_unconfirmed_claims_get_no_summary(repository, notified, llm):
    conversation = store(repository, idle=timedelta(0))

    generate_claim_summary(str(conversation.id))

    assert llm.requests == []
    assert async_to_sync(repository.get)(conversation.id).summary is None


def test_confirmed_claims_whose_summary_was_never_queued_are_queued(repository, queued):
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


# --- sweep and turns ----------------------------------------------------------------------


async def idle_conversation(repository) -> Conversation:
    last_activity = timezone.now() - ABANDONED_AFTER - timedelta(minutes=1)
    conversation = Conversation(created_at=last_activity, updated_at=last_activity)
    return await repository.create(conversation)


async def test_a_conversation_receiving_a_turn_is_not_swept(repository, engine, llm, notified):
    idle = await idle_conversation(repository)
    swept: list[int] = []
    scripted_complete = llm.complete

    async def slow_complete(**request):
        # The sweep runs while the LLM is thinking.
        swept.append(await sync_to_async(sweep_abandoned_conversations)())
        return await scripted_complete(**request)

    llm.complete = slow_complete
    llm.then(say("¿Me indica su número de póliza?"))

    result = await engine.handle_text(idle.id, "Hola, quiero dar un parte")

    assert swept == [0]
    assert result.conversation.claim.status is ClaimStatus.IN_PROGRESS
    assert [reply.text for reply in result.replies] == ["¿Me indica su número de póliza?"]


class SweptAfterLoading(DjangoConversationRepository):
    async def get(self, conversation_id):
        # The sweep closes the conversation right after the turn loaded it.
        conversation = await super().get(conversation_id)
        await sync_to_async(sweep_abandoned_conversations)()
        return conversation


async def test_a_turn_on_a_conversation_swept_meanwhile_is_told_it_closed(
    repository, llm, notified
):
    idle = await idle_conversation(repository)
    engine = ConversationEngine(llm=llm, repository=SweptAfterLoading(), sleep=no_sleep)

    result = await engine.handle_text(idle.id, "Hola")

    claim = result.conversation.claim
    assert claim.closure_reason is ClosureReason.ABANDONED
    assert [reply.text for reply in result.replies] == [templates.already_closed(claim).text]
    assert llm.requests == []


def test_a_summary_that_keeps_failing_is_recorded_and_not_requeued(
    repository, notified, queued, llm, caplog
):
    conversation = store(
        repository, idle=SUMMARY_GRACE + timedelta(minutes=1), claim=confirmed_claim()
    )
    llm.then(*[LLM_DOWN] * 4)

    generate_claim_summary.apply(args=[str(conversation.id)])

    assert len(llm.requests) == 4  # the first attempt and three retries
    assert async_to_sync(repository.get)(conversation.id).summary_failed
    assert any(r.levelname == "ERROR" and "summary" in r.message for r in caplog.records)
    assert queue_missing_summaries() == 0
    assert queued == []


def test_key_points_with_identifiers_nobody_gave_are_flagged(repository, notified, llm, caplog):
    conversation = store(repository, idle=timedelta(0), claim=confirmed_claim())
    llm.then(call(RECORD_KEY_POINTS, {"key_points": ["Choque con el 9999 ZZZ."]}))

    generate_claim_summary(str(conversation.id))

    [warning] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "plate 9999ZZZ was never given" in warning.getMessage()
