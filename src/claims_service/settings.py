"""Django settings, read from environment variables (see .env.example)."""

import os

from claims_agent.log_config import json_logging_config


def _env(name: str, default: str | None = None) -> str:
    # An empty value, as in a copied .env.example, counts as unset.
    value = os.environ.get(name) or default
    if value is None:
        raise RuntimeError(f"environment variable {name} is required")
    return value


DEBUG = _env("DJANGO_DEBUG", "false").lower() == "true"
SECRET_KEY = _env("DJANGO_SECRET_KEY", "dev-only-insecure-key" if DEBUG else None)
ALLOWED_HOSTS = _env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "channels",
    "claims_service.intake",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
ROOT_URLCONF = "claims_service.urls"
ASGI_APPLICATION = "claims_service.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": _env("POSTGRES_HOST", "localhost"),
        "PORT": _env("POSTGRES_PORT", "5432"),
        "NAME": _env("POSTGRES_DB", "claims"),
        "USER": _env("POSTGRES_USER", "claims"),
        "PASSWORD": _env("POSTGRES_PASSWORD", "claims"),
        "CONN_MAX_AGE": 0,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Europe/Madrid"

REDIS_URL = _env("REDIS_URL", "redis://localhost:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        # channels-redis blocks on BZPOPMIN for 5 s, the same as redis-py 8's default socket
        # timeout, so reads time out first; give the socket more room than the pop.
        "CONFIG": {"hosts": [{"address": REDIS_URL, "socket_timeout": 15}]},
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Keep the JSON logging configured below instead of Celery's own format.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_BEAT_SCHEDULE = {
    "sweep-abandoned-conversations": {
        "task": "claims_service.intake.tasks.sweep_abandoned_conversations",
        "schedule": 60.0,
    },
    "queue-missing-summaries": {
        "task": "claims_service.intake.tasks.queue_missing_summaries",
        "schedule": 300.0,
    },
}

OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "")
LANGFUSE_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))

LOGGING = json_logging_config(_env("LOG_LEVEL", "INFO"))
