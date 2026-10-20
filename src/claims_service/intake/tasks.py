"""Background work: the summary of confirmed claims and the abandonment sweep."""

import logging
from datetime import timedelta
from uuid import UUID

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from claims_agent.adapters.openrouter import OpenRouterChatModel
from claims_agent.domain import ClaimStatus, ClosureReason, Conversation, close
from claims_agent.engine.summary import SummaryError, summarize
from claims_agent.ports import ConcurrentUpdateError
from claims_service.intake.groups import group_name
from claims_service.intake.models import ConversationRecord
from claims_service.intake.presenters import claim_updated_event
from claims_service.intake.repository import DjangoConversationRepository, to_conversation

logger = logging.getLogger(__name__)

ABANDONED_AFTER = timedelta(minutes=10)
_ACTIVE = (ClaimStatus.IN_PROGRESS.value, ClaimStatus.PENDING_CONFIRMATION.value)


def _notify(conversation: Conversation) -> None:
    """Push the new claim state to whoever is watching the conversation."""
    async_to_sync(get_channel_layer().group_send)(
        group_name(conversation.id),
        {"type": "claim_updated", "payload": claim_updated_event(conversation)},
    )


@shared_task(
    autoretry_for=(SummaryError, ConcurrentUpdateError),
    retry_backoff=5,
    max_retries=3,
)
def generate_claim_summary(conversation_id: str) -> None:
    repository = DjangoConversationRepository()
    llm = OpenRouterChatModel.create(settings.OPENROUTER_API_KEY)

    async def run() -> Conversation | None:
        conversation = await repository.get(UUID(conversation_id))
        if conversation.claim.status is not ClaimStatus.CONFIRMED or conversation.summary:
            return None
        summary = await summarize(llm, conversation)
        return await repository.save(conversation.model_copy(update={"summary": summary}))

    conversation = async_to_sync(run)()
    if conversation is not None:
        logger.info("Claim summary generated", extra={"conversation_id": conversation_id})
        _notify(conversation)


@shared_task
def sweep_abandoned_conversations() -> int:
    """Close as incomplete every active conversation idle for ABANDONED_AFTER."""
    cutoff = timezone.now() - ABANDONED_AFTER
    repository = DjangoConversationRepository()
    closed = 0
    for record in ConversationRecord.objects.filter(status__in=_ACTIVE, updated_at__lt=cutoff):
        conversation = to_conversation(record)
        claim = close(conversation.claim, ClaimStatus.INCOMPLETE, ClosureReason.ABANDONED)
        try:
            # A conditional UPDATE on the version: a turn that lands meanwhile wins.
            saved = async_to_sync(repository.save)(conversation.model_copy(update={"claim": claim}))
        except ConcurrentUpdateError:
            continue
        closed += 1
        logger.info("Conversation abandoned", extra={"conversation_id": str(conversation.id)})
        _notify(saved)
    return closed
