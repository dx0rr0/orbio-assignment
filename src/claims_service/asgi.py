import os

from channels.routing import ProtocolTypeRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")


def _application() -> ProtocolTypeRouter:
    # Django must be set up before anything imports models, hence the imports here.
    django_application = get_asgi_application()
    from claims_service.intake import services
    from claims_service.intake.routing import lifespan_application, websocket_application

    consumer_services = services.for_consumer()
    return ProtocolTypeRouter(
        {
            "http": django_application,
            "websocket": websocket_application(consumer_services),
            "lifespan": lifespan_application(consumer_services),
        }
    )


application = _application()
