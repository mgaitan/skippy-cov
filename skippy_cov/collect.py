"""Resolve a CLI selection through pytest in an isolated collection process."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skippy_cov.plugin import pytest_collection_modifyitems, selection_key


class Collector:
    def __init__(self, targets: set[str], output: Path):
        self.targets = targets
        self.output = output

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        config.stash[selection_key] = self.targets
        pytest_collection_modifyitems(config, items)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        root = Path.cwd().resolve()
        targets = []
        for item in session.items:
            path = item.path.resolve()
            filename = path.relative_to(root) if path.is_relative_to(root) else path
            suffix = "::".join(item.nodeid.split("::")[1:])
            targets.append(f"{filename.as_posix()}::{suffix}" if suffix else filename.as_posix())
        self.output.write_text(json.dumps(targets))


def main() -> None:
    plan_path, output_path, *scopes = sys.argv[1:]
    collector = Collector(set(json.loads(Path(plan_path).read_text())), Path(output_path))
    # Discovery settings still apply. Execution-only addopts must not run tests
    # or write to the caller's coverage database while resolving node IDs.
    raise SystemExit(pytest.main(["--collect-only", "-q", "-o", "addopts=", *(scopes or ["."])], plugins=[collector]))


if __name__ == "__main__":
    main()
