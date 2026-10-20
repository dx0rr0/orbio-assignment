"""Django project: HTTP API, WebSocket turns and background tasks around the engine."""

from claims_service.celery import app as celery_app

__all__ = ["celery_app"]
