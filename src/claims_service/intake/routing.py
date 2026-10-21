"""WebSocket routes and the server's lifespan, around the services the consumer runs with."""

from collections.abc import Awaitable, Callable
from typing import Any

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


Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def lifespan_application(
    services: ConsumerServices,
) -> Callable[[dict[str, Any], Receive, Send], Awaitable[None]]:
    """Closes the services when the server shuts down (the ASGI lifespan protocol)."""

    async def application(scope: dict[str, Any], receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await services.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    return application
