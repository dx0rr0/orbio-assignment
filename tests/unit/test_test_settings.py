import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_tests_run_without_an_env_file():
    env = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}

    run = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "tests/unit/test_claim.py"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )  # fmt: skip

    assert run.returncode == 0, run.stdout + run.stderr
