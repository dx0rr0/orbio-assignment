import os

import django


def pytest_configure() -> None:
    """The project's settings, with a secret key, so that `uv run pytest` works without an .env
    file (the pure domain tests need no configuration at all). pytest-django finishes the setup
    after this hook."""
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "claims_service.settings")
    django.setup()
