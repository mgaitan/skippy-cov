from __future__ import annotations

import io
import subprocess
from pathlib import Path

from unidiff import PatchedFile, PatchSet
from unidiff.errors import UnidiffParseError

from skippy_cov.utils import checkout_root


class DiffHandlerError(Exception):
    pass


class DiffHandler:
    def __init__(self, contents: str, *, base_ref: str | None = None):
        """Parse a unified diff and optionally retain its baseline revision."""
        self.root = checkout_root()
        self.base_ref = base_ref
        self.patches = self.parse_diff(contents)

    @property
    def changed_files(self) -> set[Path]:
        return set(self.patches)

    def __getitem__(self, key: Path) -> str:
        return str(self.patches[key]).strip()

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

    def parse_diff(self, diff_text: str) -> dict[Path, PatchedFile]:
        """Map changed paths to parsed patches, rejecting unsupported diffs."""
        patches: dict[Path, PatchedFile] = {}
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
                patches[Path(path)] = patched_file
        except (UnidiffParseError, ValueError) as error:
            message = f"Invalid unified diff: {error}"
            raise DiffHandlerError(message) from error

        return patches
