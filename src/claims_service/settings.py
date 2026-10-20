"""Django settings, read from environment variables (see .env.example)."""

import os

from claims_agent.log_config import json_logging_config


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
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
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_BEAT_SCHEDULE = {
    "sweep-abandoned-conversations": {
        "task": "claims_service.intake.tasks.sweep_abandoned_conversations",
        "schedule": 60.0,
    }
}

OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY", "")
LANGFUSE_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))

LOGGING = json_logging_config(_env("LOG_LEVEL", "INFO"))
