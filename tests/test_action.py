"""Exercise the action bridge with real Git history and mocked GitHub metadata."""

import json
import runpy
import subprocess
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

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
        "GH_TOKEN": "test-token",
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
        assert command[0] != "gh", "The action must not depend on GitHub CLI"
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)

    def urlopen(request, **kwargs):
        response = MagicMock()
        payload = {"actions_caches": [cache]} if urlparse(request.full_url).path.endswith("/caches") else metadata
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
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


@pytest.mark.parametrize("token_variable", ["GH_TOKEN", "GITHUB_TOKEN"])
def test_api_authentication_and_enterprise_url(bridge, monkeypatch, token_variable):
    namespace, _ = bridge
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv(token_variable, "test-token")
    monkeypatch.setenv("GITHUB_API_URL", "https://github.example/api/v3/")
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
        assert namespace["github_api"]("repos/owner/project/actions/caches", ref="refs/heads/feature/a") == {"ok": True}
    request = urlopen.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer test-token"
    assert request.full_url.startswith("https://github.example/api/v3/repos/owner/project/actions/caches?")
    assert parse_qs(urlparse(request.full_url).query) == {"ref": ["refs/heads/feature/a"]}
    assert urlopen.call_args.kwargs["timeout"] == 30


def test_cache_pagination_reaches_eligible_second_page(history, bridge, monkeypatch):
    source, _, revisions, _, base = history
    namespace, values = bridge
    monkeypatch.chdir(source)
    monkeypatch.setenv("BASE_REF", base)
    github(monkeypatch, revisions[4])
    real_urlopen = urllib.request.urlopen
    pages = []

    def urlopen(request, **kwargs):
        query = parse_qs(urlparse(request.full_url).query)
        if urlparse(request.full_url).path.endswith("/caches"):
            pages.append(query["page"])
            if query["page"] == ["1"]:
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(
                    {"actions_caches": [{"key": "unrelated", "created_at": "2026-10-28"}] * 100}
                ).encode()
                return response
        return real_urlopen(request, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    namespace["baseline"]()
    assert pages == [["1"], ["2"]]
    assert values["baseline"] == revisions[4]


def test_api_failure_keeps_safe_fallback(history, bridge, monkeypatch, tmp_path):
    source, _, _, _, base = history
    monkeypatch.chdir(source)
    monkeypatch.setenv("BASE_REF", base)
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", [str(ACTION), "baseline"])
    with patch("urllib.request.urlopen", side_effect=HTTPError("https://api.github.com", 403, "Forbidden", {}, None)):
        runpy.run_path(str(ACTION), run_name="__main__")
    result = output.read_text().splitlines()
    assert result[0].startswith("baseline<<")
    assert result[1] == ""
    assert "HTTP Error 403" in result[4]
