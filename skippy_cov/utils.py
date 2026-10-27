from __future__ import annotations

import subprocess
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import coverage
from coverage.exceptions import CoverageException

from skippy_cov.config_handler import get_config

DEFAULT_GLOB_PATTERNS = ("test_*.py", "*_test.py")


class FilterCandidatesError(ValueError):
    def __init__(self, *, paths: list[Path]):
        self.message = f"Attempting to relativize based in multiple paths: {paths}"
        super().__init__(self.message)


@dataclass
class FileTestCandidate:
    path: Path
    tests: set[str]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, FileTestCandidate):
            raise NotImplementedError
        return self.path < other.path

    def as_set(self) -> set[str]:
        if not self.tests:
            return {self.path.as_posix()}
        tests = set()
        for test in self.tests:
            tests.add(f"{self.path.as_posix()}::{test}")
        return tests


@dataclass
class CollectedTest:
    path: Path
    name: str
    definition: Path | None = None


def select_collected_tests(tests: list[CollectedTest], candidates: list[FileTestCandidate]) -> list[CollectedTest]:
    """Match actual pytest identities, keeping inherited definitions and current parameter cases."""
    modules = {candidate.path for candidate in candidates if not candidate.tests}
    identities = {(candidate.path, test.split("[", 1)[0]) for candidate in candidates for test in candidate.tests}
    return [
        test
        for test in tests
        if test.path in modules or test.definition in modules or (test.path, test.name.split("[", 1)[0]) in identities
    ]


def is_test_file(file_path: Path) -> bool:
    """
    Checks if a file is a test file based on its name.

    Checks if a config file is present. If so, tries to use the `python_files` key
    from the config file.
    Otherwise uses the default glob pattern. (test_*.py)
    """

    # TODO: consider pytest flags such as `ignore`, `addopts`, etc...
    # config_handler.ConfigHandler should already handle it,
    #   but I want to have something working before sinking more hours into this
    # SEE: https://docs.pytest.org/en/stable/example/pythoncollection.html#changing-naming-conventions
    patterns = get_config().get_value("python_files")
    if isinstance(patterns, str):
        patterns = patterns.split()
    if not isinstance(patterns, (list, tuple)):
        patterns = DEFAULT_GLOB_PATTERNS
    return any(fnmatch(file_path.name, pattern) for pattern in patterns if isinstance(pattern, str))


def _fix_test_name(test_name: str) -> tuple[str, str]:
    """
    Removes everything after the last `|` from the test name and the first `::`
    In a file like `file.py::test_name|phase_name` the `|` separates the phase name
        and the first `::` separates the test name from the file_name
    This is because the context in coverage is always a string like `file_name.py::test_name`
        or `file_name.py::class_name::test_name`

    >>> _fix_test_name("file.py::test_name|phase_name")
    ('file.py', 'test_name')
    >>> _fix_test_name("file.py::class_name::test_name")
    ('file.py', 'class_name::test_name')
    """
    node_id = test_name if test_name.endswith("]") else test_name.rsplit("|", 1)[0]
    rhs, lhs = node_id.split("::", 1)
    if not rhs or not lhs:
        message = f"Invalid pytest context: {test_name}"
        raise ValueError(message)
    return (rhs, lhs)


def filter_by_path(
    candidates: list[FileTestCandidate],
    from_folders: list[Path],
    keep_prefix: bool = True,
) -> list[FileTestCandidate]:
    """
    Filters a list of FileTestCandidate objects based on a starting folder and depth.

    Args:
        candidates: A list of FileTestCandidate objects to filter.
        from_folders: A list of Path objects representing the starting folder.
        keep_prefix: A boolean, if true, the origial path is kept, if false it's removed
    Returns:
        A new list of FileTestCandidate objects that satisfy the filtering criteria.
    Raises:
        FilterCandidatesError: If `keep_prefix` is false and there's more than 1 path to filter,
                    Which doesn't make sense.
    """
    filtered_candidates: list[FileTestCandidate] = []

    if not keep_prefix and len(from_folders) > 1:
        raise FilterCandidatesError(paths=from_folders)

    for candidate in candidates:
        for filepath in from_folders:
            try:
                relative_path = candidate.path.resolve().relative_to(filepath.resolve())
            except ValueError:
                # The candidate is not within the from_folder, so skip it
                continue
            if relative_path == Path("."):
                relative_path = Path(candidate.path.name)
            selected_path = candidate.path if keep_prefix else Path(relative_path)
            filtered_candidates.append(FileTestCandidate(selected_path, set(candidate.tests)))
            break

    return filtered_candidates


def merge_candidates(candidates: Iterable[FileTestCandidate]) -> list[FileTestCandidate]:
    """Union node IDs per module, with whole-module selection taking precedence."""
    grouped: dict[Path, set[str]] = {}
    for candidate in candidates:
        if candidate.path not in grouped:
            grouped[candidate.path] = set(candidate.tests)
        elif not candidate.tests:
            grouped[candidate.path].clear()
        elif grouped[candidate.path]:
            grouped[candidate.path].update(candidate.tests)
    return [FileTestCandidate(path, grouped[path]) for path in sorted(grouped)]


class CoverageMapError(ValueError):
    """Coverage data cannot safely identify tests for this checkout."""


class CoverageMap:
    db: coverage.CoverageData

    def __init__(self, filepath: Path, *, source_root: Path | None = None):
        if not filepath.is_file():
            message = f"Missing coverage file: {filepath}"
            raise CoverageMapError(message)
        self.root = Path.cwd().resolve()
        self._validate_working_directory()
        self.source_root = source_root.resolve() if source_root else self.root
        self.db = coverage.CoverageData(str(filepath.resolve()))
        try:
            self.db.read()
            contexts = self.db.measured_contexts()
            if not any(contexts):
                message = "Coverage has no pytest contexts; collect with --cov-context=test"
                raise CoverageMapError(message)
            for context in contexts - {""}:
                self._relative_path(_fix_test_name(context)[0])
        except (OSError, CoverageException, ValueError) as error:
            message = f"Invalid coverage data or pytest context in {filepath}: {error}"
            raise CoverageMapError(message) from error
        self.filenames: dict[Path, list[str]] = defaultdict(list)
        self.test_files = {self._relative_path(_fix_test_name(context)[0]) for context in contexts if context}
        self._contexts: dict[Path, dict[int, list[str]]] = {}
        for filename in self.db.measured_files():
            self.filenames[self._relative_path(filename)].append(filename)

    def _validate_working_directory(self) -> None:
        try:
            checkout_root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return
        if self.root != Path(checkout_root).resolve():
            message = f"Run skippy-cov from the repository root: {checkout_root} (current directory: {self.root})"
            raise CoverageMapError(message)

    def _relative_path(self, filename: str) -> Path:
        path = Path(filename)
        if not path.is_absolute():
            return path
        try:
            return path.relative_to(self.source_root)
        except ValueError as error:
            message = f"Cannot map {filename} to the recorded source root {self.source_root}"
            raise CoverageMapError(message) from error

    def contexts_by_lineno(self, filepath: Path) -> dict[int, list[str]]:
        relative_path = filepath.relative_to(self.root) if filepath.is_absolute() else filepath
        if relative_path in self._contexts:
            return self._contexts[relative_path]
        filenames = self.filenames.get(relative_path)
        if filenames is None:
            return {}
        contexts: dict[int, list[str]] = defaultdict(list)
        for filename in filenames:
            for line, line_contexts in self.db.contexts_by_lineno(filename).items():
                contexts[line].extend(line_contexts)
        self._contexts[relative_path] = dict(contexts)
        return self._contexts[relative_path]

    def get_tests(self, filepath: Path, *, lines: set[int] | None = None) -> list[FileTestCandidate]:
        found_tests: defaultdict[Path, set[str]] = defaultdict(set)
        for line, line_tests in self.contexts_by_lineno(filepath).items():
            if lines is not None and line not in lines:
                continue
            for test in line_tests:
                if test:
                    src, test = _fix_test_name(test)
                    found_tests[self._relative_path(src)].add(test)
        return [FileTestCandidate(path=filepath, tests=tests) for (filepath, tests) in found_tests.items()]
