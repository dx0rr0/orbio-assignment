"""Background work: the summary of confirmed claims and the abandonment sweep."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from asgiref.sync import async_to_sync
from celery import Task, shared_task
from channels.layers import get_channel_layer
from django.utils import timezone

from claims_agent.domain import ClaimStatus, ClosureReason, Conversation, Role, close
from claims_agent.engine.hallucinations import check_summary
from claims_agent.engine.summary import SummaryError, summarize
from claims_agent.ports import ChatModel, ConcurrentUpdateError, ConversationRepository, Telemetry
from claims_service.intake.groups import group_name
from claims_service.intake.models import ConversationRecord
from claims_service.intake.presenters import claim_updated_event
from claims_service.intake.repository import to_conversation

logger = logging.getLogger(__name__)

ABANDONED_AFTER = timedelta(minutes=10)
# A confirmed claim without a summary this long after confirmation lost its task (e.g. the
# broker was down); after SUMMARY_GIVE_UP_AFTER it is left to a human.
SUMMARY_GRACE = timedelta(minutes=5)
SUMMARY_GIVE_UP_AFTER = timedelta(days=1)
_ACTIVE = (ClaimStatus.IN_PROGRESS.value, ClaimStatus.PENDING_CONFIRMATION.value)


@dataclass(frozen=True)
class TaskServices:
    """What the tasks run with: `services.for_tasks` builds them for each worker process (see
    `claims_service.celery`), the tests build their own."""

    repository: ConversationRepository
    telemetry: Telemetry
    # Called once per summary: a client is bound to the event loop it first ran on, and each
    # task runs in its own (async_to_sync).
    new_chat_model: Callable[[], ChatModel]
    # Pushes the new claim state to whoever is watching the conversation.
    notify: Callable[[Conversation], None]
    queue_summary: Callable[[str], None]


_services: TaskServices | None = None


def configure(services: TaskServices) -> None:
    """Run the tasks of this process with `services`."""
    global _services
    _services = services


def _configured() -> TaskServices:
    if _services is None:
        raise RuntimeError("the tasks have no services: call tasks.configure() first")
    return _services


def notify_watchers(conversation: Conversation) -> None:
    """Push the new claim state to whoever is watching the conversation."""
    async_to_sync(get_channel_layer().group_send)(
        group_name(conversation.id),
        {"type": "claim_updated", "payload": claim_updated_event(conversation)},
    )


class _SummaryTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        """After the last retry: say so, and mark the conversation so that nobody waits for a
        summary (nor `queue_missing_summaries` queues it again)."""
        conversation_id = args[0]
        logger.error(
            "Claim summary failed after retries: %s",
            exc,
            extra={"conversation_id": conversation_id},
        )
        repository = _configured().repository

        async def mark_failed() -> None:
            conversation = await repository.get(UUID(conversation_id))
            await repository.save(conversation.model_copy(update={"summary_failed": True}))

        try:
            async_to_sync(mark_failed)()
        except Exception:
            logger.exception(
                "Could not mark the summary as failed", extra={"conversation_id": conversation_id}
            )


@shared_task(
    base=_SummaryTask,
    autoretry_for=(SummaryError, ConcurrentUpdateError),
    retry_backoff=5,
    max_retries=3,
)
def generate_claim_summary(conversation_id: str) -> None:
    services = _configured()
    repository = services.repository
    llm = services.new_chat_model()
    telemetry = services.telemetry

    async def run() -> Conversation | None:
        conversation = await repository.get(UUID(conversation_id))
        if conversation.claim.status is not ClaimStatus.CONFIRMED or conversation.summary:
            return None
        summary = await summarize(llm, conversation)
        _check_summary(summary.key_points, conversation, telemetry)
        return await repository.save(conversation.model_copy(update={"summary": summary}))

    try:
        with telemetry.trace("claim_summary", session_id=conversation_id) as trace:
            conversation = async_to_sync(run)()
            if conversation is not None and conversation.summary is not None:
                trace.update(output=conversation.summary.model_dump(mode="json"))
    finally:
        telemetry.flush()
    if conversation is not None:
        logger.info("Claim summary generated", extra={"conversation_id": conversation_id})
        services.notify(conversation)


def _check_summary(
    key_points: tuple[str, ...], conversation: Conversation, telemetry: Telemetry
) -> None:
    """Like the turns' replies, the LLM-written key points are checked, never blocked."""
    declarant_said = " ".join(
        m.content for m in conversation.messages if m.role is Role.USER and m.content
    )
    findings = check_summary(key_points, conversation.claim, declarant_said)
    telemetry.score(
        "summary_hallucination_check", 0.0 if findings else 1.0, "; ".join(findings) or None
    )
    if findings:
        logger.warning(
            "Possible hallucination in the claim summary: %s",
            "; ".join(findings),
            extra={"conversation_id": str(conversation.id)},
        )


@shared_task
def sweep_abandoned_conversations() -> int:
    """Close as incomplete every active conversation idle for ABANDONED_AFTER."""
    cutoff = timezone.now() - ABANDONED_AFTER
    services = _configured()
    closed = 0
    for record in ConversationRecord.objects.filter(status__in=_ACTIVE, updated_at__lt=cutoff):
        conversation = to_conversation(record)
        claim = close(conversation.claim, ClaimStatus.INCOMPLETE, ClosureReason.ABANDONED)
        try:
            # A conditional UPDATE on the version: a turn that saved since (it marks the
            # conversation active before calling the LLM) wins.
            saved = async_to_sync(services.repository.save)(
                conversation.model_copy(update={"claim": claim})
            )
        except ConcurrentUpdateError:
            continue
        closed += 1
        logger.info("Conversation abandoned", extra={"conversation_id": str(conversation.id)})
        services.notify(saved)
    return closed


@shared_task
def queue_missing_summaries() -> int:
    """Queue the summary of confirmed claims that have none (an outbox sweep): the web process
    queues it on confirmation, but does not hold the call up if the broker is down."""
    now = timezone.now()
    services = _configured()
    lost = ConversationRecord.objects.filter(
        status=ClaimStatus.CONFIRMED.value,
        document__summary=None,
        document__summary_failed=False,
        # A turn on a confirmed claim saves nothing, so this is when it was confirmed.
        updated_at__lt=now - SUMMARY_GRACE,
        updated_at__gte=now - SUMMARY_GIVE_UP_AFTER,
    ).values_list("id", flat=True)
    queued = 0
    for conversation_id in lost:
        services.queue_summary(str(conversation_id))
        queued += 1
    if queued:
        logger.warning("Queued %d lost claim summaries", queued)
    return queued
