"""Small bridge between GitHub Actions outputs and the skippy-cov CLI."""

# ruff: noqa: C901, TRY003, TRY004

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


def output(**values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in values.items():
            delimiter = uuid.uuid4().hex
            stream.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def prepare_history(ref):
    depth = int(os.environ.get("HISTORY_DEPTH", "100"))
    if depth < 1:
        raise ValueError("History depth must be a positive integer")
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"], text=True, capture_output=True, check=True
    ).stdout.strip()
    available = subprocess.run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], text=True, capture_output=True)
    if shallow == "true" or available.returncode:
        head = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
        target = available.stdout.strip() if available.returncode == 0 else ref
        subprocess.run(
            ["git", "fetch", "--no-tags", f"--depth={depth}", "origin", head, f"+{target}:refs/skippy-cov/base"],
            text=True,
            capture_output=True,
            check=True,
        )
        return "refs/skippy-cov/base"
    return ref


def baseline():
    ref = os.environ["BASE_REF"]
    branch = os.environ["BASE_BRANCH"]
    workflow = os.environ["WORKFLOW"]
    prefix = os.environ["CACHE_PREFIX"]
    if not ref or ref.startswith("-") or not branch or not workflow or not prefix:
        raise ValueError("A valid base-ref, base-branch, workflow and cache-prefix are required")
    ref = prepare_history(ref)
    repository = os.environ["GITHUB_REPOSITORY"]
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "-X",
            "GET",
            f"repos/{repository}/actions/caches",
            "-f",
            f"ref=refs/heads/{branch}",
            "-f",
            f"key={prefix}-",
            "-f",
            "per_page=100",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    caches = [cache for page in json.loads(result.stdout) for cache in page["actions_caches"]]
    for cache in sorted(caches, key=lambda item: item["created_at"], reverse=True):
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)-([0-9a-f]{{40}})", cache["key"])
        if not match or cache["ref"] != f"refs/heads/{branch}":
            continue
        run_id, revision = match.groups()
        if any(
            subprocess.run(["git", "merge-base", "--is-ancestor", revision, target], capture_output=True).returncode
            for target in ("HEAD", ref)
        ):
            continue
        run = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"], text=True, capture_output=True, check=False
        )
        if run.returncode:
            continue
        metadata = json.loads(run.stdout)
        if (
            metadata["head_sha"] == revision
            and metadata["head_branch"] == branch
            and metadata["event"] == "push"
            and metadata["conclusion"] == "success"
            and metadata["path"].split("@", 1)[0] == workflow
        ):
            output(baseline=revision, **{"cache-key": cache["key"]})
            return
    raise ValueError(
        "No eligible baseline from a successful full workflow with proven ancestry (history may be insufficient)"
    )


def select():
    revision = os.environ["BASELINE"]
    if not revision:
        raise ValueError(os.environ["BASELINE_ERROR"] or "Cannot resolve baseline revision")
    if os.environ["CACHE_HIT"] != "true":
        raise ValueError("No exact coverage cache for the baseline revision")
    directory = Path(os.environ["BASELINE_PATH"])
    if (directory / "sha.txt").read_text().strip() != revision:
        raise ValueError("Coverage cache revision does not match the full run")
    coverage = directory / ".coverage"
    if not coverage.is_file():
        raise ValueError("Coverage cache does not contain the requested database")
    granularity = os.environ["GRANULARITY"]
    groups = json.loads(os.environ["COLLECTION_GROUPS"])
    pytest_args = json.loads(os.environ["PYTEST_ARGS"])
    if granularity not in {"module", "test"}:
        raise ValueError("Granularity must be module or test")
    if not isinstance(groups, list) or any(
        not isinstance(group, list) or not group or any(not isinstance(path, str) or not path for path in group)
        for group in groups
    ):
        raise ValueError("Collection groups must be a JSON array of nonempty arrays of paths")
    if not isinstance(pytest_args, list) or any(not isinstance(arg, str) or not arg for arg in pytest_args):
        raise ValueError("Pytest arguments must be a JSON array of strings")
    if granularity != "test" and (groups or pytest_args):
        raise ValueError("Collection groups and pytest arguments require test granularity")
    command = [
        sys.executable,
        "-m",
        "skippy_cov",
        "--diff",
        f"{revision}..HEAD",
        "--coverage-file",
        str(coverage),
        "--granularity",
        granularity,
        "--format",
        "json",
    ]
    if os.environ["SOURCE_ROOT"]:
        command.extend(["--source-root", os.environ["SOURCE_ROOT"]])
    for group in groups:
        command.extend(["--collect-group", *group])
    command.extend(f"--pytest-arg={arg}" for arg in pytest_args)
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    targets = json.loads(result.stdout)
    if not isinstance(targets, dict):
        raise ValueError("Invalid selection output")
    output(selected="true", targets=json.dumps(targets, separators=(",", ":")), reason="")


if __name__ == "__main__":
    try:
        {"baseline": baseline, "select": select}[sys.argv[1]]()
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        message = (
            error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        )
        if sys.argv[1] == "baseline":
            output(baseline="", reason=message)
        else:
            output(selected="false", targets="", reason=message)
