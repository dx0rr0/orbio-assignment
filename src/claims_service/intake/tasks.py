"""Background work: the summary of confirmed claims and the abandonment sweep."""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from asgiref.sync import async_to_sync
from celery import Task, shared_task
from channels.layers import get_channel_layer
from django.utils import timezone

from claims_agent.domain import ClaimStatus, ClosureReason, Conversation, close
from claims_agent.engine.hallucinations import report_summary
from claims_agent.engine.summary import SummaryError, summarize
from claims_agent.ports import ChatModel, ConcurrentUpdateError, Telemetry
from claims_service.intake.groups import ClaimUpdatedMessage, group_name
from claims_service.intake.presenters import claim_updated_event
from claims_service.intake.repository import DjangoConversationRepository

logger = logging.getLogger(__name__)

ABANDONED_AFTER = timedelta(minutes=10)
# A confirmed claim without a summary this long after confirmation lost its task (e.g. the
# broker was down); after SUMMARY_GIVE_UP_AFTER it is left to a human.
SUMMARY_GRACE = timedelta(minutes=5)
SUMMARY_GIVE_UP_AFTER = timedelta(days=1)


@dataclass(frozen=True)
class TaskServices:
    """What the tasks run with: `services.for_tasks` builds them for each worker process (see
    `claims_service.celery`), the tests build their own."""

    # The sweeps query it beyond the engine's port.
    repository: DjangoConversationRepository
    telemetry: Telemetry
    # Called once per summary: a client is bound to the event loop it first ran on, and each
    # task runs in its own (async_to_sync).
    open_chat_model: Callable[[], AbstractAsyncContextManager[ChatModel]]
    # Pushes the new claim state to whoever is watching the conversation.
    notify: Callable[[Conversation], None]
    queue_summary: Callable[[str], None]


@dataclass
class _Configuration:
    services: TaskServices | None = None


# Celery calls the tasks by name, so their dependencies are set once per worker process.
_configuration = _Configuration()


def configure(services: TaskServices) -> None:
    """Run the tasks of this process with `services`."""
    _configuration.services = services


def _configured() -> TaskServices:
    if _configuration.services is None:
        raise RuntimeError("the tasks have no services: call tasks.configure() first")
    return _configuration.services


def notify_watchers(conversation: Conversation) -> None:
    """Push the new claim state to whoever is watching the conversation."""
    message: ClaimUpdatedMessage = {
        "type": "claim_updated",
        "payload": claim_updated_event(conversation),
    }
    async_to_sync(get_channel_layer().group_send)(group_name(conversation.id), message)


def _notify(services: TaskServices, conversation: Conversation) -> None:
    # Best effort: the change is already saved, and a watcher sees it when it reconnects.
    try:
        services.notify(conversation)
    except Exception:
        logger.exception(
            "Could not notify the watchers", extra={"conversation_id": str(conversation.id)}
        )


class _SummaryTask(Task):
    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[str, ...],
        kwargs: dict[str, object],
        einfo: object,
    ) -> None:
        """On an exception the task does not retry (any other, or the last retry's): say so,
        and mark the conversation so that nobody waits for a summary (nor
        `queue_missing_summaries` queues it again)."""
        conversation_id = args[0]
        logger.error(
            "Claim summary failed after retries: %s",
            exc,
            extra={"conversation_id": conversation_id},
        )
        repository = _configured().repository

        async def mark_failed() -> None:
            conversation = await repository.get(UUID(conversation_id))
            if conversation.summary is not None:
                return  # it failed after the summary was saved
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
    telemetry = services.telemetry

    async def run() -> Conversation | None:
        conversation = await repository.get(UUID(conversation_id))
        if conversation.claim.status is not ClaimStatus.CONFIRMED or conversation.summary:
            return None
        async with services.open_chat_model() as llm:
            summary = await summarize(llm, conversation)
        # Like the turns' replies, the LLM-written key points are checked, never blocked.
        report_summary(summary.key_points, conversation, telemetry)
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
        _notify(services, conversation)


@shared_task
def sweep_abandoned_conversations() -> int:
    """Close as incomplete every active conversation idle for ABANDONED_AFTER."""
    services = _configured()
    idle = async_to_sync(services.repository.active_idle_since)(timezone.now() - ABANDONED_AFTER)
    closed = 0
    for conversation in idle:
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
        _notify(services, saved)
    return closed


@shared_task
def queue_missing_summaries() -> int:
    """Queue the summary of confirmed claims that have none (an outbox sweep): the web process
    queues it on confirmation, but does not hold the call up if the broker is down."""
    now = timezone.now()
    services = _configured()
    lost = async_to_sync(services.repository.confirmed_without_summary)(
        confirmed_from=now - SUMMARY_GIVE_UP_AFTER, confirmed_before=now - SUMMARY_GRACE
    )
    for conversation_id in lost:
        services.queue_summary(str(conversation_id))
    if lost:
        logger.warning("Queued %d lost claim summaries", len(lost))
    return len(lost)
