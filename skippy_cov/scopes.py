"""Map edits to baseline runtime scopes, retaining pytest's recorded identities."""

from __future__ import annotations

import ast
import copy
import io
import logging
import tokenize
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from skippy_cov.diff_handler import DiffHandler
from skippy_cov.utils import CoverageMap, FileTestCandidate

logger = logging.getLogger(__name__)


class Granularity(StrEnum):
    module = "module"
    test = "test"


class ScopeSelectionError(ValueError):
    """Historical coverage cannot establish all affected consumers."""


class FileSelectionRequired(ValueError):
    """A change needs the wider file-consumer selection."""


@dataclass
class Scope:
    name: tuple[str, ...]
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef

    @property
    def start(self) -> int:
        return min([self.node.lineno, *(decorator.lineno for decorator in self.node.decorator_list)])

    @property
    def end(self) -> int:
        return self.node.end_lineno or self.node.lineno

    @property
    def header(self) -> str:
        node = copy.copy(self.node)
        node.body = []
        return ast.dump(node)


def source_scopes(source: bytes) -> dict[tuple[str, ...], Scope]:
    scopes: dict[tuple[str, ...], Scope] = {}

    def visit(node: ast.AST, parent: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parent = (*parent, node.name)
            if parent in scopes:
                message = f"Ambiguous repeated scope: {'.'.join(parent)}"
                raise FileSelectionRequired(message)
            scopes[parent] = Scope(parent, node)
        for child in ast.iter_child_nodes(node):
            visit(child, parent)

    visit(ast.parse(source), ())
    return scopes


def runtime_scope(scopes: dict[tuple[str, ...], Scope], line: int) -> Scope:
    containing = [scope for scope in scopes.values() if scope.start <= line <= scope.end]
    if containing:
        scope = max(containing, key=lambda candidate: len(candidate.name))
        if not isinstance(scope.node, ast.ClassDef) and line >= scope.node.body[0].lineno:
            return scope
    message = f"Structural change outside a function body at line {line}"
    raise FileSelectionRequired(message)


def code_lines(source: bytes) -> set[int]:
    """Ignore comments and spacing while retaining every line of string literals."""
    ignored = {
        tokenize.ENCODING,
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    return {
        line
        for token in tokenize.tokenize(io.BytesIO(source).readline)
        if token.type not in ignored
        for line in range(token.start[0], token.end[0] + 1)
    }


def changed_scopes(diff: DiffHandler, path: Path) -> tuple[dict[tuple[str, ...], Scope], set[tuple[str, ...]]]:
    """Use old coordinates for removals and new coordinates only to match additions."""
    old_source, new_source = diff.base_source(path), path.read_bytes()
    old_scopes, new_scopes = source_scopes(old_source), source_scopes(new_source)
    old_code, new_code = code_lines(old_source), code_lines(new_source)
    affected: set[tuple[str, ...]] = set()
    for hunk in diff.patches[path]:
        for line in hunk:
            if line.is_removed and line.source_line_no in old_code:
                scope = runtime_scope(old_scopes, line.source_line_no)
            elif line.is_added and line.target_line_no in new_code:
                scope = runtime_scope(new_scopes, line.target_line_no)
            else:
                continue
            old_scope = old_scopes.get(scope.name)
            new_scope = new_scopes.get(scope.name)
            if old_scope is None or new_scope is None or old_scope.header != new_scope.header:
                message = f"New, deleted, or structurally changed scope: {'.'.join(scope.name)}"
                raise FileSelectionRequired(message)
            if ast.dump(old_scope.node) != ast.dump(new_scope.node):
                affected.add(scope.name)
    return old_scopes, affected


def affected_lines(diff: DiffHandler, path: Path, coverage_map: CoverageMap) -> set[int]:
    """Find every baseline runtime line of the affected functions."""
    old_scopes, affected = changed_scopes(diff, path)
    contexts = coverage_map.contexts_by_lineno(path)
    selected_lines: set[int] = set()
    for name in affected:
        scope = old_scopes[name]
        lines = set(range(scope.node.body[0].lineno, scope.end + 1))
        # A nested function's body runs independently of the function defining it.
        for nested in old_scopes.values():
            if len(nested.name) > len(name) and nested.name[: len(name)] == name:
                lines.difference_update(range(nested.start, nested.end + 1))
        scope_contexts = {context for line in lines for context in contexts.get(line, [])}
        if any(context.endswith(("|setup", "|teardown")) for context in scope_contexts):
            message = f"Cannot determine all shared-fixture consumers of {'.'.join(name)} in {path}"
            raise ScopeSelectionError(message)
        if not scope_contexts or any(not context.endswith("|run") for context in scope_contexts):
            message = f"Unknown or import-time coverage for {'.'.join(name)} in {path}"
            raise FileSelectionRequired(message)
        selected_lines.update(lines)
    return selected_lines


def select_scope_consumers(diff: DiffHandler, path: Path, coverage_map: CoverageMap) -> list[FileTestCandidate]:
    """Prefer individual consumers, widening structural edits to whole modules."""
    try:
        lines = affected_lines(diff, path, coverage_map)
    except FileSelectionRequired as error:
        contexts = coverage_map.contexts_by_lineno(path)
        if any(
            context.endswith(("|setup", "|teardown"))
            for line_contexts in contexts.values()
            for context in line_contexts
        ):
            message = f"Cannot determine all shared-fixture consumers of {path}"
            raise ScopeSelectionError(message) from error
        candidates = coverage_map.get_tests(path)
        if not candidates:
            message = f"No test contexts cover {path}"
            raise ScopeSelectionError(message) from error
        logger.warning("skippy-cov: %s; selecting all recorded file consumers of %s", error, path)
        return [FileTestCandidate(candidate.path, set()) for candidate in candidates]
    return (
        [
            FileTestCandidate(candidate.path, {test.split("[", 1)[0] for test in candidate.tests})
            for candidate in coverage_map.get_tests(path, lines=lines)
        ]
        if lines
        else []
    )
