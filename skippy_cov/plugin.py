import inspect
import warnings
from pathlib import Path

import pytest

from skippy_cov import SelectionError
from skippy_cov.__main__ import get_base_ref, get_diff_content, run
from skippy_cov.diff_handler import DiffHandlerError
from skippy_cov.scopes import Granularity
from skippy_cov.utils import CoverageMapError

selection_key = pytest.StashKey[set[str]]()


def pytest_addoption(parser):
    group = parser.getgroup("skippy-cov", "Options for skippy coverage-based collection")
    group.addoption("--skippy-cov", action="store_true", help="Enable conservative coverage-based selection")
    group.addoption("--skippy-cov-diff", help="Path to a diff file or git ref to compare")
    group.addoption("--skippy-cov-coverage-file", type=Path, default=Path(".coverage"))
    group.addoption("--skippy-cov-source-root", type=Path, help="Original checkout root for coverage paths")
    group.addoption("--skippy-cov-granularity", choices=list(Granularity), default=Granularity.module)
    group.addoption("--skippy-cov-base-ref", help="Coverage revision when --skippy-cov-diff is a patch file")
    group.addoption("--skippy-cov-keep-prefix", dest="skippy_cov_keep_prefix", action="store_true", default=True)
    group.addoption(
        "--skippy-cov-strip-prefix",
        dest="skippy_cov_keep_prefix",
        action="store_false",
        help="Deprecated compatibility option; pytest scopes are preserved.",
    )


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
    if not config.getoption("skippy_cov_keep_prefix"):
        warnings.warn(
            pytest.PytestWarning("skippy-cov: --skippy-cov-strip-prefix is deprecated; preserving pytest scopes"),
            stacklevel=2,
        )
    try:
        granularity = Granularity(config.getoption("skippy_cov_granularity"))
        selected = run(
            get_diff_content(config.getoption("skippy_cov_diff")),
            config.getoption("skippy_cov_coverage_file"),
            None,
            True,
            source_root=config.getoption("skippy_cov_source_root"),
            granularity=granularity,
            base_ref=(
                get_base_ref(config.getoption("skippy_cov_diff"), config.getoption("skippy_cov_base_ref"))
                if granularity == Granularity.test
                else None
            ),
        )
    except (CoverageMapError, SelectionError, DiffHandlerError, OSError, ValueError, SyntaxError) as error:
        warnings.warn(pytest.PytestWarning(f"skippy-cov: {error}; running the original test selection"), stacklevel=2)
        return
    if granularity == Granularity.test:
        config.stash[selection_key] = selected
        return
    targets = scoped_targets(selected, config.args, Path.cwd())
    if not targets:
        pytest.exit("skippy-cov: no tests affected within the requested selection", returncode=5)
    config.args = targets


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Intersect current pytest discovery with impact, including inherited changed tests."""
    selected = config.stash.get(selection_key, None)
    if selected is None:
        return
    root = Path.cwd().resolve()
    if any(not item.path.resolve().is_relative_to(root) for item in items):
        warnings.warn(
            pytest.PytestWarning("skippy-cov: tests outside the checkout; keeping the original selection"), stacklevel=2
        )
        return
    modules = {target for target in selected if "::" not in target}
    # Run all current parameter cases of an affected logical test. This keeps new
    # cases without depending on IDs that may have changed since the baseline.
    tests = {
        (filename, test.split("[", 1)[0])
        for filename, separator, test in (target.partition("::") for target in selected)
        if separator
    }
    kept, deselected = [], []
    for item in items:
        path = item.path.resolve().relative_to(root).as_posix()
        suffix = "::".join(item.nodeid.split("::")[1:])
        identity = (path, suffix.split("[", 1)[0])
        definition = None
        function = getattr(item, "obj", None)
        if inspect.isfunction(function) or inspect.ismethod(function):
            source = inspect.getsourcefile(inspect.unwrap(function))
            if source and Path(source).resolve().is_relative_to(root):
                definition = Path(source).resolve().relative_to(root).as_posix()
        if path in modules or definition in modules or identity in tests:
            kept.append(item)
        else:
            deselected.append(item)
    items[:] = kept
    if deselected:
        config.hook.pytest_deselected(items=deselected)
