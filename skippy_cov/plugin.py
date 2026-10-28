import inspect
import json
import sys
import warnings
from pathlib import Path

import pytest

from skippy_cov import SelectionError, select_tests_to_run
from skippy_cov.__main__ import get_base_ref, get_diff_content, run
from skippy_cov.diff_handler import DiffHandler, DiffHandlerError
from skippy_cov.scopes import Granularity
from skippy_cov.utils import CollectedTest, CoverageMap, CoverageMapError, checkout_root, select_collected_tests

plan_key = pytest.StashKey[tuple[DiffHandler, CoverageMap]]()
report_key = pytest.StashKey[dict]()
worker_reports_key = pytest.StashKey[list[dict | None]]()


def decision(config, status, reason_code, reason, *, selected=None, deselected=None):
    config.stash[report_key].update(
        status=status,
        reason_code=reason_code,
        reason=reason,
        selected=selected,
        deselected=deselected,
    )


def summary(report):
    text = f"skippy-cov: {report['status']} ({report['reason_code']}): {report['reason']}"
    for name in ("selected", "deselected"):
        if report[name] is not None:
            text += f"; {name}={report[name]}"
    return text


def write_report(config):
    if hasattr(config, "workerinput"):
        return
    path = config.getoption("skippy_cov_report")
    if path is None:
        return
    try:
        path.write_text(json.dumps(config.stash[report_key], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        warnings.warn(pytest.PytestWarning(f"skippy-cov: cannot write report {path}: {error}"), stacklevel=2)


def pytest_sessionfinish(session):
    config = session.config
    if report_key not in config.stash:
        return
    if hasattr(config, "workerinput"):
        config.workeroutput["skippy_cov_report"] = config.stash[report_key]
        return
    reports = config.stash.get(worker_reports_key, [])
    if reports:
        first = reports[0]
        if first is not None and all(report == first for report in reports):
            config.stash[report_key] = first
        else:
            decision(
                config,
                "incomplete",
                "worker_reports_incomplete",
                "Worker selection reports are missing, disagree, or a worker crashed; selection counts are unknown",
            )
    write_report(config)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    config = node.config
    if report_key not in config.stash:
        return
    reports = config.stash.setdefault(worker_reports_key, [])
    reports.append(None if error else node.workeroutput.get("skippy_cov_report"))


def pytest_terminal_summary(terminalreporter):
    report = terminalreporter.config.stash.get(report_key, None)
    if report is not None and not hasattr(terminalreporter.config, "workerinput"):
        terminalreporter.write_sep("-", summary(report))


def pytest_addoption(parser):
    group = parser.getgroup("skippy-cov", "Options for skippy coverage-based collection")
    group.addoption("--skippy-cov", action="store_true", help="Enable conservative coverage-based selection")
    group.addoption("--skippy-cov-diff", help="Path to a diff file or git ref to compare")
    group.addoption(
        "--skippy-cov-report", type=Path, help="Write the selection decision as JSON (requires --skippy-cov)"
    )
    group.addoption("--skippy-cov-coverage-file", type=Path, default=Path(".coverage"))
    group.addoption(
        "--skippy-cov-source-root",
        type=Path,
        help="Recording directory within checkout, or original root for relocated absolute paths",
    )
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
    config.stash[report_key] = {
        "schema_version": 1,
        "granularity": config.getoption("skippy_cov_granularity"),
        "baseline": {
            "coverage_file": str(config.getoption("skippy_cov_coverage_file")),
            "source_root": str(config.getoption("skippy_cov_source_root"))
            if config.getoption("skippy_cov_source_root")
            else None,
            "base_ref": config.getoption("skippy_cov_base_ref"),
        },
    }
    decision(config, "fallback", "selection_incomplete", "Selection did not complete")
    if not config.getoption("skippy_cov_keep_prefix"):
        warnings.warn(
            pytest.PytestWarning("skippy-cov: --skippy-cov-strip-prefix is deprecated; preserving pytest scopes"),
            stacklevel=2,
        )
    try:
        granularity = Granularity(config.getoption("skippy_cov_granularity"))
        if granularity == Granularity.test:
            diff = DiffHandler(
                get_diff_content(config.getoption("skippy_cov_diff")),
                base_ref=get_base_ref(config.getoption("skippy_cov_diff"), config.getoption("skippy_cov_base_ref")),
            )
            coverage = CoverageMap(
                config.getoption("skippy_cov_coverage_file"), source_root=config.getoption("skippy_cov_source_root")
            )
            # Snapshot affected coverage before pytest-cov can overwrite the
            # baseline during normal collection or test execution.
            for path in diff.changed_files:
                coverage.contexts_by_lineno(path)
            config.stash[plan_key] = (diff, coverage)
            return
        selected = run(
            get_diff_content(config.getoption("skippy_cov_diff")),
            config.getoption("skippy_cov_coverage_file"),
            None,
            True,
            source_root=config.getoption("skippy_cov_source_root"),
        )
    except (CoverageMapError, SelectionError, DiffHandlerError, OSError, ValueError, SyntaxError) as error:
        decision(config, "fallback", "selection_error", str(error))
        warnings.warn(pytest.PytestWarning(f"skippy-cov: {error}; running the original test selection"), stacklevel=2)
        return
    targets = scoped_targets(selected, config.args, Path.cwd())
    if not targets:
        decision(config, "empty", "no_affected_tests", "No tests affected within the requested selection", selected=0)
        write_report(config)
        if not hasattr(config, "workerinput"):
            print(summary(config.stash[report_key]), file=sys.stderr)
        pytest.exit("skippy-cov: no tests affected within the requested selection", returncode=5)
    decision(config, "applied", "coverage_selection", "Narrowed collection to affected modules")
    config.args = targets


def describe_item(item: pytest.Item, root: Path) -> CollectedTest:
    path = item.path.resolve()
    filename = path.relative_to(root) if path.is_relative_to(root) else path
    definition = None
    function = getattr(item, "obj", None)
    if inspect.isfunction(function) or inspect.ismethod(function):
        source = inspect.getsourcefile(inspect.unwrap(function))
        if source:
            path = Path(source).resolve()
            definition = path.relative_to(root) if path.is_relative_to(root) else path
    return CollectedTest(filename, "::".join(item.nodeid.split("::")[1:]), definition)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Classify tests through pytest discovery, then intersect impact with the original scope."""
    plan = config.stash.get(plan_key, None)
    if plan is None:
        return
    root = checkout_root()
    if any(not item.path.resolve().is_relative_to(root) for item in items):
        decision(
            config, "fallback", "outside_checkout", "Tests outside the checkout", selected=len(items), deselected=0
        )
        warnings.warn(
            pytest.PytestWarning("skippy-cov: tests outside the checkout; keeping the original selection"), stacklevel=2
        )
        return
    discovered = [describe_item(item, root) for item in items]
    test_files = {test.path for test in discovered} | {test.definition for test in discovered if test.definition}
    diff, coverage = plan
    try:
        selected = select_tests_to_run(diff, coverage, granularity=Granularity.test, test_files=test_files)
    except (SelectionError, DiffHandlerError, OSError, ValueError, SyntaxError) as error:
        decision(config, "fallback", "selection_error", str(error), selected=len(items), deselected=0)
        warnings.warn(pytest.PytestWarning(f"skippy-cov: {error}; running the original test selection"), stacklevel=2)
        return
    identities = {(test.path, test.name) for test in select_collected_tests(discovered, selected)}
    deselected = [
        item for item, test in zip(items, discovered, strict=True) if (test.path, test.name) not in identities
    ]
    items[:] = [item for item, test in zip(items, discovered, strict=True) if (test.path, test.name) in identities]
    decision(
        config,
        "applied" if items else "empty",
        "coverage_selection" if items else "no_affected_tests",
        "Selected affected tests" if items else "No tests affected within the requested selection",
        selected=len(items),
        deselected=len(deselected),
    )
    if deselected:
        config.hook.pytest_deselected(items=deselected)


def pytest_collection_finish(session):
    report = session.config.stash.get(report_key, None)
    if report is not None and report["selected"] is None:
        report["selected"] = len(session.items)
        if report["status"] == "fallback":
            report["deselected"] = 0
