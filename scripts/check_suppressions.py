"""Fail if any source file silences a linter, formatter or type checker inline.

    uv run python scripts/check_suppressions.py

Fix the cause instead; the few rule exceptions the project accepts live in pyproject.toml and
eslint.config.js, by file, where they are reviewed.
"""

import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("src", "tests", "evals", "scripts", "frontend/src")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs"}
# Built from pieces so that this file does not match itself.
SUPPRESSIONS = re.compile(
    "|".join(
        (
            r"#\s*no" + "qa",
            r"#\s*type:\s*ig" + "nore",
            r"#\s*pyright:\s*ig" + "nore",
            r"#\s*fmt:\s*(?:off|skip)",
            r"#\s*pragma:\s*no " + "cover",
            r"eslint-dis" + "able",
            r"@ts-(?:ignore|expect-error|nocheck)",
        )
    )
)


def sources() -> Iterator[Path]:
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*"):
            if path.suffix in SOURCE_SUFFIXES and "node_modules" not in path.parts:
                yield path
    yield from ROOT.glob("frontend/*.[jt]s")


def main() -> int:
    found = 0
    for path in sources():
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if SUPPRESSIONS.search(line):
                print(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
                found += 1
    if found:
        print(f"{found} inline suppression(s): fix the cause instead.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
