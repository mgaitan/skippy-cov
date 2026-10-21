from __future__ import annotations

import ast
import re
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


def function_body_lines(file_path: Path) -> set[int]:
    """Find runtime function bodies without reconstructing pytest node IDs."""
    tree = ast.parse(file_path.read_bytes())
    return {
        line
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno is not None
        for line in range(node.body[0].lineno, node.end_lineno + 1)
    }


def test_function_lines(file_path: Path) -> set[int]:
    tree = ast.parse(file_path.read_bytes())
    lines = set()
    for node in ast.walk(tree):
        if (
            not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            or not node.name.startswith("test")
            or node.end_lineno is None
        ):
            continue
        if any(
            not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"
                and isinstance(decorator.func.value, ast.Attribute)
                and decorator.func.value.attr == "mark"
                and isinstance(decorator.func.value.value, ast.Name)
                and decorator.func.value.value.id == "pytest"
            )
            for decorator in node.decorator_list
        ):
            continue
        start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
        lines.update(range(start, node.end_lineno + 1))
    return lines


def is_safe_test_deletion(line: str) -> bool:
    line = line.lstrip()
    return (
        not line.strip()
        or line.startswith("#")
        or re.match(r"def\s+test\w*\s*\(", line) is not None
        or re.match(r"@pytest\.mark\.parametrize\s*\(", line) is not None
    )


def validate_function_changes(
    file_path: Path,
    patch: PatchedFile,
    contexts: dict[int, list[str]],
    *,
    tests_only: bool,
) -> None:
    eligible_lines = test_function_lines(file_path) if tests_only else function_body_lines(file_path)
    for hunk in patch:
        for is_context, block in groupby(hunk, key=lambda line: line.is_context):
            if is_context:
                continue
            changes = list(block)
            added_lines = {line.target_line_no for line in changes if line.is_added and line.target_line_no is not None}
            removed_lines = {
                line.source_line_no for line in changes if line.is_removed and line.source_line_no is not None
            }
            removed_values = {
                line.source_line_no: line.value
                for line in changes
                if line.is_removed and line.source_line_no is not None
            }
            added_values = {
                line.target_line_no: line.value for line in changes if line.is_added and line.target_line_no is not None
            }
            invalid_test_deletion = tests_only and any(
                not is_safe_test_deletion(removed_values[line]) and not contexts.get(line) for line in removed_lines
            )
            if (
                (not added_lines and not removed_lines)
                or (not tests_only and (not added_lines or not removed_lines or not added_lines <= eligible_lines))
                or (
                    tests_only
                    and any(
                        line not in eligible_lines and not is_safe_test_deletion(added_values[line])
                        for line in added_lines
                    )
                )
                or invalid_test_deletion
            ):
                message = f"Changed imports, declarations or untracked code in {file_path}"
                raise SelectionError(message)
            if any(
                (not tests_only and not contexts.get(line))
                or (
                    not (tests_only and is_safe_test_deletion(removed_values[line]))
                    and any(not context.endswith("|run") for context in contexts.get(line, []))
                )
                for line in removed_lines
            ):
                message = f"Changed lines in {file_path} have import-time, shared-fixture or unknown coverage"
                raise SelectionError(message)


def select_tests_to_run(diff_handler: DiffHandler, coverage_map: CoverageMap) -> list[FileTestCandidate]:
    """Select test modules, refusing changes with unknown or shared dependencies."""
    selected_modules: set[Path] = set()
    known_tests = {
        coverage_map._relative_path(Path(context.rsplit("|", 1)[0].split("::", 1)[0]))
        for context in coverage_map.db.measured_contexts()
        if context
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
        candidates = coverage_map.get_tests(file_path)
        if is_test_module:
            validate_function_changes(file_path, patch, contexts, tests_only=True)
            selected_modules.add(file_path)
        else:
            try:
                validate_function_changes(file_path, patch, contexts, tests_only=False)
            except SelectionError:
                if not candidates:
                    raise
                selected_modules.update(candidate.path for candidate in candidates)
                continue
        if not is_test_module and not candidates:
            message = f"No test contexts cover {file_path}"
            raise SelectionError(message)
        selected_modules.update(candidate.path for candidate in candidates)
    for test_path in selected_modules:
        if not test_path.is_file():
            message = f"Selected test module no longer exists: {test_path}"
            raise SelectionError(message)
    return [FileTestCandidate(path, set()) for path in sorted(selected_modules)]
