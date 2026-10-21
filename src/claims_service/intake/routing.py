"""WebSocket routes, around the services the consumer runs with."""

from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator, OriginValidator
from django.urls import path

from claims_service.intake.consumers import ConversationConsumer
from claims_service.intake.services import ConsumerServices


def websocket_application(services: ConsumerServices) -> OriginValidator:
    consumer = ConversationConsumer.as_asgi(services=services)
    # Only pages served from ALLOWED_HOSTS may open a conversation (no cross-site sockets).
    return AllowedHostsOriginValidator(
        URLRouter(
            [
                path("ws/conversations/", consumer),
                path("ws/conversations/<uuid:conversation_id>/", consumer),
            ]
        )
    )
