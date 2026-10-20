import subprocess
from pathlib import Path

import pytest

from skippy_cov.__main__ import get_default_branch, get_diff_content
from skippy_cov.diff_handler import DiffHandlerError


@pytest.fixture
def diverged_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tests"], check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.org"], check=True)
    Path("source.py").write_text("def price():\n    return 1\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "base"], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature"], check=True, capture_output=True)
    Path("source.py").write_text("def price():\n    return 2\n")
    subprocess.run(["git", "commit", "-am", "feature"], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], check=True, capture_output=True)
    Path("base_only.py").write_text("BASE_ONLY = True\n")
    subprocess.run(["git", "add", "."], check=True)
    subprocess.run(["git", "commit", "-m", "base advanced"], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "feature"], check=True, capture_output=True)


def test_single_ref_uses_pr_triple_dot_comparison(diverged_repository):
    diff = get_diff_content("main")
    assert "+    return 2" in diff
    assert "base_only.py" not in diff


def test_explicit_two_dot_comparison_is_preserved(diverged_repository):
    assert "base_only.py" in get_diff_content("main..HEAD")


def test_default_branch_is_detected_from_local_remote_head(diverged_repository):
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"], check=True)
    subprocess.run(["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True)
    assert get_default_branch() == "origin/main"


def test_directory_with_branch_name_is_not_read_as_a_patch(diverged_repository):
    Path("main").mkdir()
    assert "+    return 2" in get_diff_content("main")


def test_invalid_ref_is_an_error_instead_of_system_exit(diverged_repository):
    with pytest.raises(DiffHandlerError, match="Failed to get git diff"):
        get_diff_content("missing-ref")


def test_ref_cannot_be_interpreted_as_a_git_option(diverged_repository):
    with pytest.raises(DiffHandlerError, match="must not start"):
        get_diff_content("--output=unexpected-file")
    assert not Path("unexpected-file").exists()
