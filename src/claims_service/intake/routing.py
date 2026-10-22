"""WebSocket routes and the server's lifespan, around the services the consumer runs with."""

import asyncio
from collections.abc import Awaitable, Callable

from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, LifespanScope
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


def lifespan_application(
    services: ConsumerServices,
) -> Callable[[LifespanScope, ASGIReceiveCallable, ASGISendCallable], Awaitable[None]]:
    """Synthesizes the greeting in the background when the server starts, and closes the
    services when it shuts down (the ASGI lifespan protocol)."""

    async def application(
        scope: LifespanScope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        warm_up: asyncio.Task[None] | None = None
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                warm_up = asyncio.create_task(services.voice.warm_up())
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if warm_up is not None:
                    warm_up.cancel()
                await services.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    return application
