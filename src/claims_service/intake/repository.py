"""The storage ports (conversations, policies, the reference sequence) on the Django ORM and
Postgres."""

from datetime import datetime
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import connection

from claims_agent.domain import ClaimStatus, Conversation, Policy
from claims_agent.ports import ConcurrentUpdateError, ConversationNotFoundError
from claims_service.intake.models import ConversationRecord, PolicyRecord

_ACTIVE = (ClaimStatus.IN_PROGRESS.value, ClaimStatus.PENDING_CONFIRMATION.value)


def _fields(conversation: Conversation) -> dict[str, object]:
    return {
        "status": conversation.claim.status.value,
        "document": conversation.model_dump(mode="json", exclude={"version"}),
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _to_conversation(record: ConversationRecord) -> Conversation:
    return Conversation.model_validate(record.document | {"version": record.version})


class DjangoConversationRepository:
    """The engine's port, plus the queries of the service's own API and background tasks."""

    async def create(self, conversation: Conversation) -> Conversation:
        await ConversationRecord.objects.acreate(
            id=conversation.id, version=conversation.version, **_fields(conversation)
        )
        return conversation

    async def get(self, conversation_id: UUID) -> Conversation:
        try:
            record = await ConversationRecord.objects.aget(id=conversation_id)
        except ConversationRecord.DoesNotExist:
            raise ConversationNotFoundError(conversation_id) from None
        return _to_conversation(record)

    async def save(self, conversation: Conversation) -> Conversation:
        """One conditional UPDATE: it only applies if nobody saved since this was loaded."""
        updated = await ConversationRecord.objects.filter(
            id=conversation.id, version=conversation.version
        ).aupdate(version=conversation.version + 1, **_fields(conversation))
        if not updated:
            if not await ConversationRecord.objects.filter(id=conversation.id).aexists():
                raise ConversationNotFoundError(conversation.id)
            raise ConcurrentUpdateError(conversation.id)
        return conversation.model_copy(update={"version": conversation.version + 1})

    async def list_recent(self, *, status: ClaimStatus | None, limit: int) -> list[Conversation]:
        """The last `limit` saved, most recent first, optionally only those in `status`."""
        records = ConversationRecord.objects.order_by("-updated_at")
        if status is not None:
            records = records.filter(status=status.value)
        return [_to_conversation(record) async for record in records[:limit]]

    async def active_idle_since(self, cutoff: datetime) -> list[Conversation]:
        """In progress or pending confirmation, and not saved since `cutoff`."""
        records = ConversationRecord.objects.filter(status__in=_ACTIVE, updated_at__lt=cutoff)
        return [_to_conversation(record) async for record in records]

    async def confirmed_without_summary(
        self, *, confirmed_from: datetime, confirmed_before: datetime
    ) -> list[UUID]:
        """The ids of the claims confirmed in [`confirmed_from`, `confirmed_before`) that have
        no summary, nor one that failed."""
        ids = ConversationRecord.objects.filter(
            status=ClaimStatus.CONFIRMED.value,
            document__summary=None,
            document__summary_failed=False,
            # A turn on a confirmed claim saves nothing, so this is when it was confirmed.
            updated_at__gte=confirmed_from,
            updated_at__lt=confirmed_before,
        ).values_list("id", flat=True)
        return [conversation_id async for conversation_id in ids]


class DjangoPolicyCatalog:
    async def get_policy(self, number: str) -> Policy | None:
        record = await PolicyRecord.objects.filter(number=number).afirst()
        if record is None:
            return None
        return Policy(
            number=record.number,
            holder_name=record.holder_name,
            plate=record.plate,
            start_date=record.start_date,
            end_date=record.end_date,
        )


class PostgresReferenceSequence:
    async def next_reference_sequence(self, year: int) -> int:
        return await sync_to_async(_next_sequence)(year)


def _next_sequence(year: int) -> int:
    # An atomic upsert, so concurrent confirmations never get the same number.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO reference_sequence (year, last) VALUES (%s, 1)
            ON CONFLICT (year) DO UPDATE SET last = reference_sequence.last + 1
            RETURNING last
            """,
            [year],
        )
        (last,) = cursor.fetchone()
    return int(last)
