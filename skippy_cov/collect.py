"""Resolve a CLI selection through pytest in an isolated collection process."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skippy_cov.plugin import describe_item
from skippy_cov.utils import checkout_root


class Collector:
    def __init__(self, output: Path):
        self.output = output

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        root = checkout_root()
        tests = []
        for item in session.items:
            test = describe_item(item, root)
            tests.append(
                {
                    "path": test.path.as_posix(),
                    "name": test.name,
                    "definition": test.definition.as_posix() if test.definition else None,
                }
            )
        self.output.write_text(json.dumps(tests))


def main() -> None:
    output_path, arguments, *scopes = sys.argv[1:]
    collector = Collector(Path(output_path))
    # Discovery settings still apply. Execution-only addopts must not run tests
    # or write to the caller's coverage database while resolving node IDs.
    raise SystemExit(
        pytest.main([*json.loads(arguments), "--collect-only", "-q", "-o", "addopts=", *scopes], plugins=[collector])
    )


if __name__ == "__main__":
    main()
