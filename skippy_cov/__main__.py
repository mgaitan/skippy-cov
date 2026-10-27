import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
from enum import StrEnum
from pathlib import Path

from skippy_cov import __version__, select_tests_to_run
from skippy_cov.diff_handler import DiffHandler, DiffHandlerError
from skippy_cov.scopes import Granularity
from skippy_cov.utils import (
    CollectedTest,
    CoverageMap,
    FileTestCandidate,
    filter_by_path,
    merge_candidates,
    select_collected_tests,
)

logger = logging.getLogger(__name__)


class Format(StrEnum):
    pytest = "pytest"
    json = "json"
    lines = "lines"


def collect_current_tests(scopes: list[Path] | None) -> list[CollectedTest]:
    """Let pytest resolve inheritance, custom discovery, and parameter IDs for the CLI."""
    with tempfile.TemporaryDirectory(prefix="skippy-collection-") as directory:
        root = Path(directory)
        output = root / "tests.json"
        result = subprocess.run(
            [sys.executable, "-m", "skippy_cov.collect", str(output), *map(str, scopes or [])],
            capture_output=True,
            text=True,
            env=os.environ | {"COVERAGE_FILE": str(root / ".coverage"), "PYTEST_ADDOPTS": ""},
            check=False,
        )
        if result.returncode not in {0, 5} or not output.is_file():
            message = f"Cannot resolve current pytest collection: {result.stdout}\n{result.stderr}"
            raise ValueError(message)
        tests = json.loads(output.read_text())
    return [
        CollectedTest(Path(test["path"]), test["name"], Path(test["definition"]) if test["definition"] else None)
        for test in tests
    ]


def run(
    diff: str,
    coverage_file: Path,
    relative_to: list[Path] | None,
    keep_prefix: bool,
    fmt: Format = Format.pytest,
    display: bool = False,
    *,
    source_root: Path | None = None,
    granularity: Granularity = Granularity.module,
    base_ref: str | None = None,
    collect: bool = False,
) -> set[str]:
    """Select tests and optionally print deterministic CLI output."""
    diff_handler = DiffHandler(diff, base_ref=base_ref)
    coverage_map = CoverageMap(coverage_file, source_root=source_root)
    discovered = collect_current_tests(relative_to) if collect else []
    test_files = {test.path for test in discovered} | {test.definition for test in discovered if test.definition}
    selected_tests = select_tests_to_run(diff_handler, coverage_map, granularity=granularity, test_files=test_files)
    if collect:
        selected_tests = [
            FileTestCandidate(test.path, {test.name} if test.name else set())
            for test in select_collected_tests(discovered, selected_tests)
        ]
    if relative_to:
        selected_tests = filter_by_path(selected_tests, relative_to, keep_prefix)
    selected_tests = merge_candidates(selected_tests)
    output = {target for candidate in selected_tests for target in candidate.as_set()}
    if display:
        if fmt == Format.pytest:
            print(shlex.join(sorted(output)))
        elif fmt == Format.lines:
            print("\n".join(sorted(output)))
        elif fmt == Format.json:
            print(json.dumps({candidate.path.as_posix(): sorted(candidate.tests) for candidate in selected_tests}))
    return output


def get_default_branch() -> str:
    """Find the default ref locally without contacting a remote or parsing locale-dependent output."""
    try:
        return subprocess.check_output(
            ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        for reference in (
            "refs/heads/main",
            "refs/heads/master",
            "refs/remotes/origin/main",
            "refs/remotes/origin/master",
        ):
            try:
                subprocess.check_output(
                    ["git", "show-ref", "--verify", reference],
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.CalledProcessError):
                continue
            return reference.removeprefix("refs/heads/").removeprefix("refs/remotes/")
    return "main"


def get_diff_content(diff_arg: str | None = None) -> str:
    """Read a patch or compare a single ref to HEAD using PR-style triple-dot semantics."""
    if diff_arg and Path(diff_arg).is_file():
        return Path(diff_arg).read_text()
    diff_ref = diff_arg or get_default_branch()
    if diff_ref.startswith("-"):
        message = "A diff ref must not start with '-'"
        raise DiffHandlerError(message)
    if ".." not in diff_ref:
        diff_ref = f"{diff_ref}...HEAD"
    try:
        return subprocess.check_output(
            ["git", "diff", "--no-ext-diff", "--no-color", "--no-renames", diff_ref, "--"],
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        message = f"Failed to get git diff for {diff_ref}: {error}"
        raise DiffHandlerError(message) from error


def get_base_ref(diff_arg: str | None, base_ref: str | None = None) -> str | None:
    """Resolve the diff's old side; a raw patch needs an explicit baseline ref."""

    def git(*arguments: str) -> str:
        try:
            return subprocess.check_output(["git", *arguments], text=True, stderr=subprocess.PIPE).strip()
        except (OSError, subprocess.CalledProcessError) as error:
            message = f"Cannot resolve baseline revision: {error}"
            raise DiffHandlerError(message) from error

    def commit(ref: str) -> str:
        return git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")

    explicit = commit(base_ref) if base_ref else None
    if diff_arg and Path(diff_arg).is_file():
        return explicit
    reference = diff_arg or get_default_branch()
    if "..." in reference:
        old, new = reference.split("...", 1)
        resolved = git("merge-base", commit(old or "HEAD"), commit(new or "HEAD"))
    elif ".." in reference:
        old, new = reference.split("..", 1)
        resolved = commit(old or "HEAD")
    else:
        new = "HEAD"
        resolved = git("merge-base", commit(reference), commit(new))
    if commit(new or "HEAD") != commit("HEAD"):
        message = "Individual test selection requires the diff's new revision to be HEAD"
        raise DiffHandlerError(message)
    if explicit and explicit != resolved:
        message = "The explicit baseline revision does not match the diff's old revision"
        raise DiffHandlerError(message)
    return resolved


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Select pytest modules based on diff and historical coverage.")
    parser.add_argument("--diff", help="Patch file, git ref (compared with ...HEAD), or explicit range.")
    parser.add_argument("--coverage-file", type=Path, default=Path(".coverage"), help="Coverage SQLite database.")
    parser.add_argument("--source-root", type=Path, help="Original checkout root for absolute coverage paths.")
    parser.add_argument("--granularity", type=Granularity, choices=list(Granularity), default=Granularity.module)
    parser.add_argument("--base-ref", help="Coverage revision; required for individual selection with a patch file.")
    parser.add_argument("--relative-to", type=Path, nargs="+", help="Limit selection to these files or directories.")
    parser.add_argument("--keep-prefix", dest="keep_prefix", default=True, action="store_true")
    parser.add_argument("--strip-prefix", dest="keep_prefix", action="store_false")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--format", type=Format, default=Format.pytest, choices=list(Format))
    arguments = parser.parse_args(argv)
    if arguments.debug:
        logging.basicConfig(level=logging.DEBUG)
    try:
        run(
            get_diff_content(arguments.diff),
            arguments.coverage_file,
            arguments.relative_to,
            arguments.keep_prefix,
            arguments.format,
            display=True,
            source_root=arguments.source_root,
            granularity=arguments.granularity,
            base_ref=get_base_ref(arguments.diff, arguments.base_ref)
            if arguments.granularity == Granularity.test
            else None,
            collect=arguments.granularity == Granularity.test,
        )
    except (DiffHandlerError, OSError, ValueError, SyntaxError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
