from __future__ import annotations

import ast
from importlib.metadata import version
from itertools import groupby
from pathlib import Path

from unidiff import PatchedFile

from skippy_cov.diff_handler import DiffHandler
from skippy_cov.utils import CoverageMap, FileTestCandidate, is_test_file

__version__ = version("skippy-cov")


class SelectionError(ValueError):
    """The diff has dependencies that historical coverage cannot identify."""


def discover_tests_in_file(file_path: Path) -> FileTestCandidate | None:
    """Select a whole test module and leave discovery to pytest."""
    if is_test_file(file_path) and file_path.is_file():
        return FileTestCandidate(file_path, set())
    return None


def function_body_lines(file_path: Path, *, tests_only: bool) -> set[int]:
    """Find runtime function bodies without reconstructing pytest node IDs."""
    tree = ast.parse(file_path.read_bytes())
    return {
        line
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (not tests_only or (node.name.startswith("test") and not node.decorator_list))
        and node.end_lineno is not None
        for line in range(node.body[0].lineno, node.end_lineno + 1)
    }


def validate_function_changes(
    file_path: Path,
    patch: PatchedFile,
    contexts: dict[int, list[str]],
    *,
    tests_only: bool,
) -> None:
    body_lines = function_body_lines(file_path, tests_only=tests_only)
    for hunk in patch:
        for is_context, block in groupby(hunk, key=lambda line: line.is_context):
            if is_context:
                continue
            changes = list(block)
            added_lines = {line.target_line_no for line in changes if line.is_added and line.target_line_no is not None}
            removed_lines = {
                line.source_line_no for line in changes if line.is_removed and line.source_line_no is not None
            }
            if not added_lines or not removed_lines or not added_lines <= body_lines:
                message = f"Changed imports, declarations or untracked code in {file_path}"
                raise SelectionError(message)
            if any(
                (not tests_only and not contexts.get(line))
                or any(not context.endswith("|run") for context in contexts.get(line, []))
                for line in removed_lines
            ):
                message = f"Changed lines in {file_path} have import-time, shared-fixture or unknown coverage"
                raise SelectionError(message)


def select_tests_to_run(diff_handler: DiffHandler, coverage_map: CoverageMap) -> list[FileTestCandidate]:
    """Select test modules, refusing changes with unknown or shared dependencies."""
    selected_modules: set[Path] = set()
    known_tests = {
        Path(context.rsplit("|", 1)[0].split("::", 1)[0]) for context in coverage_map.db.measured_contexts() if context
    }
    for file_path in sorted(diff_handler.changed_files):
        patch = diff_handler.patches[file_path]
        if (
            file_path.suffix != ".py"
            or file_path.name in {"conftest.py", "__init__.py"}
            or not file_path.is_file()
            or patch.is_added_file
            or patch.is_removed_file
            or patch.is_rename
            or not patch
        ):
            message = f"Cannot determine test impact for {file_path}; run the original test selection"
            raise SelectionError(message)
        is_test_module = file_path in known_tests
        contexts = coverage_map.contexts_by_lineno(file_path)
        validate_function_changes(file_path, patch, contexts, tests_only=is_test_module)
        candidates = coverage_map.get_tests(file_path)
        if is_test_module:
            selected_modules.add(file_path)
        elif not candidates:
            message = f"No test contexts cover {file_path}"
            raise SelectionError(message)
        selected_modules.update(candidate.path for candidate in candidates)
    for test_path in selected_modules:
        if not test_path.is_file():
            message = f"Selected test module no longer exists: {test_path}"
            raise SelectionError(message)
    return [FileTestCandidate(path, set()) for path in sorted(selected_modules)]
