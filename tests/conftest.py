import hashlib

import pytest
from django.conf import settings


def pytest_configure(config: pytest.Config) -> None:
    """One test database per checkout: runs in two worktrees at once would otherwise create and
    drop the same database under each other."""
    checkout = hashlib.sha256(str(config.rootpath).encode()).hexdigest()[:8]
    database = settings.DATABASES["default"]
    database["TEST"] = {"NAME": f"test_{database['NAME']}_{checkout}"}
