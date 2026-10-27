from __future__ import annotations

import io
import logging
import subprocess
from collections import defaultdict
from pathlib import Path

from unidiff import PatchedFile, PatchSet
from unidiff.errors import UnidiffParseError

logger = logging.getLogger(__name__)


class DiffHandlerError(Exception):
    pass


class DiffHandler:
    def __init__(self, contents: str, *, base_ref: str | None = None):
        """
        Args:
            diff_file: The contents of the file containing the full output of a 'git diff' command.

        Raises:
            FileNotFoundError: If the specified diff file does not exist.
        """
        self.patches: dict[Path, PatchedFile] = {}
        self.base_ref = base_ref
        self.changes = self.parse_diff(contents)

    @property
    def changed_files(self) -> set[Path]:
        return set(self.changes.keys())

    def __getitem__(self, key: Path) -> str:
        return self.changes[key]

    def base_source(self, path: Path) -> bytes:
        """Read source in the coverage revision without importing it."""
        if not self.base_ref or self.base_ref.startswith("-"):
            message = "Individual test selection requires a baseline revision (--base-ref for patch files)"
            raise DiffHandlerError(message)
        try:
            return subprocess.check_output(
                ["git", "show", f"{self.base_ref}:{path.as_posix()}"], stderr=subprocess.PIPE
            )
        except (OSError, subprocess.CalledProcessError) as error:
            message = f"Cannot read baseline source for {path} at {self.base_ref}"
            raise DiffHandlerError(message) from error

    def parse_diff(self, diff_text: str) -> defaultdict[Path, str]:
        """
        Parses the full text output of 'git diff' using the 'unidiff' library.

        Args:
            diff_text: A string containing the full output of a 'git diff' command.

        Returns:
            A set of unique file paths affected by the diff and the affected diff
        """
        mapped_changes: defaultdict[Path, str] = defaultdict(str)
        try:
            patch_set = PatchSet(io.StringIO(diff_text))
            if diff_text.strip() and not patch_set:
                message = "No supported patch found in the diff"
                raise DiffHandlerError(message)

            for patched_file in patch_set:
                if patched_file.is_removed_file:
                    path = patched_file.source_file
                    if path.startswith("a/"):
                        path = path[2:]
                else:
                    path = patched_file.target_file
                    if path.startswith("b/"):
                        path = path[2:]
                mapped_changes[Path(path)] = str(patched_file).strip()
                self.patches[Path(path)] = patched_file
        except (UnidiffParseError, ValueError) as error:
            message = f"Invalid unified diff: {error}"
            raise DiffHandlerError(message) from error

        return mapped_changes
