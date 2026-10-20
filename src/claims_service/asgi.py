import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")
# Django must be set up before anything imports models.
django_application = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.urls import path  # noqa: E402

from claims_service.intake.consumers import ConversationConsumer  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_application,
        "websocket": URLRouter(
            [
                path("ws/conversations/", ConversationConsumer.as_asgi()),
                path("ws/conversations/<uuid:conversation_id>/", ConversationConsumer.as_asgi()),
            ]
        ),
    }
)
