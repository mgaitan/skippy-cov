import argparse
import json
import logging
import shlex
import subprocess
from enum import StrEnum
from pathlib import Path

from skippy_cov import __version__, select_tests_to_run
from skippy_cov.diff_handler import DiffHandler, DiffHandlerError
from skippy_cov.utils import CoverageMap, filter_by_path, merge_candidates

logger = logging.getLogger(__name__)


class Format(StrEnum):
    pytest = "pytest"
    json = "json"
    lines = "lines"


def run(
    diff: str,
    coverage_file: Path,
    relative_to: list[Path] | None,
    keep_prefix: bool,
    fmt: Format = Format.pytest,
    display: bool = False,
    *,
    source_root: Path | None = None,
) -> set[str]:
    """Select tests and optionally print deterministic CLI output."""
    diff_handler = DiffHandler(diff)
    coverage_map = CoverageMap(coverage_file, source_root=source_root)
    selected_tests = select_tests_to_run(diff_handler, coverage_map)
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Select pytest modules based on diff and historical coverage.")
    parser.add_argument("--diff", help="Patch file, git ref (compared with ...HEAD), or explicit range.")
    parser.add_argument("--coverage-file", type=Path, default=Path(".coverage"), help="Coverage SQLite database.")
    parser.add_argument("--source-root", type=Path, help="Original checkout root for absolute coverage paths.")
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
        )
    except (DiffHandlerError, OSError, ValueError, SyntaxError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
