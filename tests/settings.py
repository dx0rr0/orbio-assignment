"""Settings for the test run: the project's, with a secret key, so that `uv run pytest` works
without an .env file (the pure domain tests need no configuration at all)."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-insecure-key")

from claims_service.settings import *  # noqa: F403
