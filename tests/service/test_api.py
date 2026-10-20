from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.test import Client
from django.utils import timezone

from claims_agent.domain import Claim, ClaimStatus, Conversation

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client() -> Client:
    return Client()


def store(repository, claim: Claim) -> Conversation:
    conversation = Conversation.start(timezone.now()).model_copy(update={"claim": claim})
    return async_to_sync(repository.create)(conversation)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_get_conversation(client, repository):
    conversation = store(repository, Claim(declarant_name="Ana García"))

    body = client.get(f"/api/conversations/{conversation.id}").json()

    assert body["id"] == str(conversation.id)
    fields = {f["name"]: f for f in body["claim"]["fields"]}
    assert fields["declarant_name"] == {
        "name": "declarant_name",
        "state": "valid",
        "value": "Ana García",
        "error": None,
    }


def test_unknown_conversation_is_404(client):
    assert client.get(f"/api/conversations/{uuid4()}").status_code == 404


def test_list_filters_by_status(client, repository):
    store(repository, Claim())
    confirmed = store(repository, Claim(status=ClaimStatus.CONFIRMED, reference="SIN-2026-000001"))

    body = client.get("/api/conversations", {"status": "confirmed"}).json()

    assert [c["id"] for c in body] == [str(confirmed.id)]
    assert client.get("/api/conversations", {"status": "bogus"}).status_code == 422
