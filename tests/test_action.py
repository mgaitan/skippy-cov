"""Exercise the action bridge with real Git history and mocked GitHub metadata."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ACTION = Path(__file__).resolve().parents[1] / ".github/actions/select/action.py"


def git(directory, *args):
    return subprocess.run(["git", *args], cwd=directory, text=True, capture_output=True, check=True).stdout.strip()


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.com")
    git(source, "config", "user.name", "Test")
    revisions = []
    for number in range(8):
        (source / "source.py").write_text(f"VALUE = {number}\n")
        git(source, "add", ".")
        git(source, "commit", "-m", f"Commit {number}")
        revisions.append(git(source, "rev-parse", "HEAD"))
    git(source, "checkout", "-b", "feature")
    (source / "feature.py").write_text("FEATURE = True\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "Feature")
    feature = git(source, "rev-parse", "HEAD")
    git(source, "checkout", "main")
    (source / "base.py").write_text("BASE = True\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "Advance base")
    base = git(source, "rev-parse", "HEAD")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--depth=1", "--branch=feature", source.as_uri(), str(checkout))
    return source, checkout, revisions, feature, base


@pytest.fixture
def bridge(monkeypatch):
    namespace = runpy.run_path(str(ACTION))
    values = {}
    monkeypatch.setitem(namespace["baseline"].__globals__, "output", lambda **items: values.update(items))
    for name, value in {
        "BASE_BRANCH": "main",
        "WORKFLOW": ".github/workflows/full.yml",
        "CACHE_PREFIX": "baseline-v1",
        "GITHUB_REPOSITORY": "owner/project",
    }.items():
        monkeypatch.setenv(name, value)
    return namespace, values


def github(monkeypatch, revision, workflow=".github/workflows/full.yml@refs/heads/main", **overrides):
    real_run = subprocess.run
    commands = []
    cache = {"key": f"baseline-v1-42-{revision}", "ref": "refs/heads/main", "created_at": "2026-10-27"}
    metadata = {
        "head_sha": revision,
        "head_branch": "main",
        "event": "push",
        "conclusion": "success",
        "path": workflow,
    }
    metadata.update(overrides)

    def run(command, **kwargs):
        commands.append(command)
        if command[0] != "gh":
            return real_run(command, **kwargs)
        data = [{"actions_caches": [cache]}] if command[-1] == "per_page=100" else metadata
        return subprocess.CompletedProcess(command, 0, json.dumps(data), "")

    monkeypatch.setattr(subprocess, "run", run)
    return commands


@pytest.mark.parametrize("target", ["sha", "branch"])
def test_shallow_checkout_fetches_bounded_history(history, bridge, monkeypatch, target):
    _, checkout, revisions, feature, base = history
    namespace, values = bridge
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("BASE_REF", base if target == "sha" else "refs/heads/main")
    monkeypatch.setenv("HISTORY_DEPTH", "6")
    commands = github(monkeypatch, revisions[4])
    assert git(checkout, "rev-parse", "--is-shallow-repository") == "true"
    namespace["baseline"]()
    assert values["baseline"] == revisions[4]
    assert git(checkout, "rev-parse", "HEAD") == feature
    assert git(checkout, "rev-parse", "refs/skippy-cov/base") == base
    assert git(checkout, "rev-parse", "--is-shallow-repository") == "true"
    fetches = [command for command in commands if command[:2] == ["git", "fetch"]]
    assert len(fetches) == 1
    assert "--depth=6" in fetches[0]
    assert "--no-tags" in fetches[0]


@pytest.mark.parametrize("candidate", ["too-old", "not-on-base", "not-on-head"])
def test_unproven_ancestry_rejected(history, bridge, monkeypatch, candidate):
    _, checkout, revisions, feature, base = history
    namespace, values = bridge
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("BASE_REF", base)
    monkeypatch.setenv("HISTORY_DEPTH", "2")
    revision = {"too-old": revisions[0], "not-on-base": feature, "not-on-head": base}[candidate]
    github(monkeypatch, revision)
    with pytest.raises(ValueError, match="proven ancestry"):
        namespace["baseline"]()
    assert values == {}


@pytest.mark.parametrize(
    ("workflow", "eligible"),
    [
        (".github/workflows/full.yml", True),
        (".github/workflows/full.yml@main", True),
        (".github/workflows/full.yml@refs/heads/main", True),
        (".github/workflows/other.yml@main", False),
        (".github/workflows/full.yml.extra@main", False),
    ],
)
def test_workflow_path_and_full_checkout(history, bridge, monkeypatch, workflow, eligible):
    source, _, revisions, _, base = history
    namespace, values = bridge
    monkeypatch.chdir(source)
    monkeypatch.setenv("BASE_REF", base)
    commands = github(monkeypatch, revisions[4], workflow)
    if eligible:
        namespace["baseline"]()
        assert values["baseline"] == revisions[4]
    else:
        with pytest.raises(ValueError, match="No eligible baseline"):
            namespace["baseline"]()
    assert not any(command[:2] == ["git", "fetch"] for command in commands)


@pytest.mark.parametrize(
    "overrides",
    [
        {"head_sha": "0" * 40},
        {"head_branch": "other"},
        {"event": "pull_request"},
        {"conclusion": "failure"},
        {"conclusion": None},
    ],
)
def test_workflow_provenance_still_required(history, bridge, monkeypatch, overrides):
    source, _, revisions, _, base = history
    namespace, values = bridge
    monkeypatch.chdir(source)
    monkeypatch.setenv("BASE_REF", base)
    github(monkeypatch, revisions[4], **overrides)
    with pytest.raises(ValueError, match="No eligible baseline"):
        namespace["baseline"]()
    assert values == {}


@pytest.mark.parametrize("depth", ["0", "-1", "invalid"])
def test_invalid_history_depth(bridge, monkeypatch, depth):
    namespace, _ = bridge
    monkeypatch.setenv("BASE_REF", "main")
    monkeypatch.setenv("HISTORY_DEPTH", depth)
    with pytest.raises(ValueError):
        namespace["baseline"]()


@pytest.mark.parametrize("failure", ["history", "fetch"])
def test_entrypoint_falls_back_safely(history, bridge, monkeypatch, tmp_path, failure):
    _, checkout, revisions, _, base = history
    monkeypatch.chdir(checkout)
    monkeypatch.setenv("BASE_REF", base)
    monkeypatch.setenv("HISTORY_DEPTH", "2")
    if failure == "fetch":
        git(checkout, "remote", "remove", "origin")
    github(monkeypatch, revisions[0])
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", [str(ACTION), "baseline"])
    runpy.run_path(str(ACTION), run_name="__main__")
    lines = output.read_text().splitlines()
    assert lines[0].startswith("baseline<<")
    assert lines[1] == ""
    assert lines[3].startswith("reason<<")
    assert lines[4]
    monkeypatch.setenv("BASELINE", "")
    monkeypatch.setenv("BASELINE_ERROR", lines[4])
    monkeypatch.setattr(sys, "argv", [str(ACTION), "select"])
    output.write_text("")
    runpy.run_path(str(ACTION), run_name="__main__")
    result = output.read_text().splitlines()
    assert result[0].startswith("selected<<")
    assert result[1] == "false"
    assert lines[4] in result
