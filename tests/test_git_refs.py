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


@pytest.mark.parametrize("branch", ["main", "master"])
@pytest.mark.parametrize("shallow", [False, True])
def test_default_branch_uses_fetched_refs_in_detached_checkout(
    diverged_repository, tmp_path, monkeypatch, branch, shallow
):
    if branch == "master":
        subprocess.run(["git", "branch", "-m", "main", "master"], check=True)
    checkout = tmp_path / "checkout"
    depth = ["--depth=2"] if shallow else []
    subprocess.run(
        ["git", "clone", "--branch", "feature", *depth, tmp_path.as_uri(), str(checkout)],
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(checkout)
    subprocess.run(
        ["git", "fetch", *depth, "origin", f"{branch}:refs/remotes/origin/{branch}"], check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "--detach"], check=True, capture_output=True)
    subprocess.run(["git", "branch", "-D", "feature"], check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "--no-deref", "-d", "refs/remotes/origin/HEAD"], check=True)
    assert subprocess.check_output(["git", "for-each-ref", "refs/heads"], text=True).strip() == ""
    is_shallow = subprocess.check_output(["git", "rev-parse", "--is-shallow-repository"], text=True).strip()
    assert is_shallow == str(shallow).lower()
    expected_diff = get_diff_content(f"origin/{branch}")
    assert "+    return 2" in expected_diff
    assert "base_only.py" not in expected_diff
    assert get_default_branch() == f"origin/{branch}"
    assert get_diff_content() == expected_diff


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


def test_default_branch_without_git_or_refs_uses_main(mocker):
    mocker.patch("skippy_cov.__main__.subprocess.check_output", side_effect=FileNotFoundError)
    assert get_default_branch() == "main"


def test_baseline_resolution_reports_invalid_refs(diverged_repository):
    from skippy_cov.__main__ import get_base_ref

    with pytest.raises(DiffHandlerError, match="Cannot resolve baseline revision"):
        get_base_ref("missing-ref")


def test_baseline_ranges_use_merge_base_and_reject_a_different_new_revision(diverged_repository):
    from skippy_cov.__main__ import get_base_ref

    expected = subprocess.check_output(["git", "merge-base", "main", "HEAD"], text=True).strip()
    assert get_base_ref("main...HEAD") == expected
    assert get_base_ref("...HEAD") == subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    with pytest.raises(DiffHandlerError, match="new revision to be HEAD"):
        get_base_ref("HEAD..main")
