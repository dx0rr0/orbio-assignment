"""Storage for policies and conversations (ADR-0003).

A conversation is stored as the domain's JSON document; `status` and `updated_at` are copied
into columns so that the abandonment sweep and admin queries can filter on them.
"""

from django.db import models


class PolicyRecord(models.Model):
    number = models.CharField(primary_key=True, max_length=12)
    holder_name = models.CharField(max_length=200)
    plate = models.CharField(max_length=10)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "policy"


class ConversationRecord(models.Model):
    id = models.UUIDField(primary_key=True)
    status = models.CharField(max_length=32)
    document = models.JSONField()
    version = models.PositiveIntegerField()
    # SHA-256 of the conversation's access token (see access.py); kept out of the document.
    access_token_hash = models.CharField(max_length=64, default="")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "conversation"
        indexes = [models.Index(fields=["status", "updated_at"])]


class ReferenceSequence(models.Model):
    """Last claim reference number issued each year."""

    year = models.PositiveSmallIntegerField(primary_key=True)
    last = models.PositiveIntegerField()

    class Meta:
        db_table = "reference_sequence"
