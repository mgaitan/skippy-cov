import warnings
from pathlib import Path

import pytest

from skippy_cov import SelectionError
from skippy_cov.__main__ import get_diff_content, run
from skippy_cov.diff_handler import DiffHandlerError
from skippy_cov.utils import CoverageMapError


def pytest_addoption(parser):
    group = parser.getgroup("skippy-cov", "Options for skippy coverage-based collection")
    group.addoption("--skippy-cov", action="store_true", help="Enable conservative coverage-based selection")
    group.addoption("--skippy-cov-diff", help="Path to a diff file or git ref to compare")
    group.addoption("--skippy-cov-coverage-file", type=Path, default=Path(".coverage"))
    group.addoption("--skippy-cov-source-root", type=Path, help="Original checkout root for coverage paths")
    group.addoption("--skippy-cov-keep-prefix", dest="skippy_cov_keep_prefix", action="store_true", default=True)
    group.addoption("--skippy-cov-strip-prefix", dest="skippy_cov_keep_prefix", action="store_false")


def scoped_targets(selected: set[str], original: list[str], working_directory: Path) -> list[str]:
    """Intersect selected modules with the caller's paths and exact node IDs."""
    selected_paths = {str((working_directory / target).resolve()): target for target in selected}
    targets: set[str] = set()
    for argument in original:
        requested_path = Path(argument.split("::", 1)[0]).resolve()
        if requested_path.is_dir() and "::" not in argument:
            targets.update(
                target for path, target in selected_paths.items() if Path(path).is_relative_to(requested_path)
            )
        elif str(requested_path) in selected_paths:
            targets.add(argument)
    return sorted(targets)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Narrow collection only when both coverage and the diff are trustworthy."""
    if not config.getoption("skippy_cov"):
        return
    try:
        selected = run(
            get_diff_content(config.getoption("skippy_cov_diff")),
            config.getoption("skippy_cov_coverage_file"),
            None,
            True,
            source_root=config.getoption("skippy_cov_source_root"),
        )
    except (CoverageMapError, SelectionError, DiffHandlerError, OSError, ValueError, SyntaxError) as error:
        warnings.warn(pytest.PytestWarning(f"skippy-cov: {error}; running the original test selection"), stacklevel=2)
        return
    targets = scoped_targets(selected, config.args, Path.cwd())
    if not targets:
        pytest.exit("skippy-cov: no tests affected within the requested selection", returncode=5)
    config.args = targets
