import json
import subprocess
from types import SimpleNamespace

import pytest

from skippy_cov import plugin


@pytest.mark.parametrize("mode", ["module", "test"])
@pytest.mark.parametrize("scenario", ["applied", "fallback", "empty", "disabled", "io-error"])
def test_selection_report(baseline_project, change_file, mode, scenario):
    project = baseline_project
    change_file()
    report = project.path / "report.json"
    if scenario == "empty":
        (project.path / "changes.diff").write_text("")
    if scenario == "io-error":
        report.mkdir()
    arguments = [
        f"--skippy-cov-granularity={mode}",
        "--skippy-cov-diff=changes.diff",
        f"--skippy-cov-report={report}",
        "legacy.py",
        "test_other.py",
    ]
    if scenario != "disabled":
        arguments.append("--skippy-cov")
    if scenario == "fallback":
        arguments.append("--skippy-cov-coverage-file=missing")
    # Test mode needs old source for nonempty diffs; missing base is a
    # collection-time fallback, not a precollection coverage failure.
    result = project.runpytest_subprocess(*arguments)
    output = result.stdout.str() + result.stderr.str()
    if scenario == "disabled":
        result.assert_outcomes(failed=1, passed=2)
        assert not report.exists()
        assert "skippy-cov:" not in output
        return
    if scenario == "io-error":
        assert result.ret == 1
        assert "cannot write report" in output
        return
    data = json.loads(report.read_text())
    assert data["schema_version"] == 1
    assert data["granularity"] == mode
    assert data["baseline"] == {
        "coverage_file": "missing" if scenario == "fallback" else ".coverage",
        "source_root": None,
        "base_ref": None,
    }
    expected = "fallback" if mode == "test" and scenario == "applied" else scenario
    assert data["status"] == expected
    assert f"skippy-cov: {expected} ({data['reason_code']})" in output
    if expected == "empty":
        assert result.ret == 5
        assert data["selected"] == 0
        assert data["deselected"] == (3 if mode == "test" else None)
    elif expected == "fallback":
        result.assert_outcomes(failed=1, passed=2)
        assert data["reason_code"] == "selection_error"
        assert data["selected"] == 3
        assert data["deselected"] == 0
        assert data["reason"]
    else:
        result.assert_outcomes(failed=1, passed=1)
        assert data["selected"] == 2
        assert data["deselected"] is None


@pytest.mark.parametrize("mode", ["module", "test"])
@pytest.mark.parametrize("scenario", ["applied", "fallback", "empty"])
def test_xdist_selection_report(baseline_project, change_file, mode, scenario):
    project = baseline_project
    for args in (
        ["init", "-q"],
        ["add", "*.py"],
        ["-c", "user.name=Tests", "-c", "user.email=tests@example.com", "commit", "-qm", "baseline"],
    ):
        subprocess.run(["git", *args], cwd=project.path, check=True)
    change_file(source="def price():\n    return 1 + 0\n")
    if scenario == "empty":
        (project.path / "changes.diff").write_text("")
    arguments = [
        "-n2",
        "--skippy-cov",
        f"--skippy-cov-granularity={mode}",
        "--skippy-cov-diff=changes.diff",
        "--skippy-cov-base-ref=HEAD",
        "--skippy-cov-report=report.json",
        "legacy.py",
        "test_other.py",
    ]
    if scenario == "fallback":
        arguments.append("--skippy-cov-coverage-file=missing")
    result = project.runpytest_subprocess(*arguments)
    data = json.loads((project.path / "report.json").read_text())
    assert data["status"] == scenario
    output = result.stdout.str() + result.stderr.str()
    assert output.count(f"skippy-cov: {scenario} ({data['reason_code']})") == 1
    if scenario == "empty":
        assert result.ret == 5
        assert data["selected"] == 0
        assert data["deselected"] == (3 if mode == "test" else None)
    elif scenario == "fallback":
        result.assert_outcomes(passed=3)
        assert data["selected"] == 3
        assert data["deselected"] == 0
    else:
        expected = 1 if mode == "test" else 2
        result.assert_outcomes(passed=expected)
        assert data["selected"] == expected
        assert data["deselected"] == (2 if mode == "test" else None)


@pytest.mark.parametrize("scenario", ["match", "mismatch", "missing", "crash", "disabled"])
def test_worker_report_aggregation(tmp_path, scenario):
    report_path = tmp_path / "report.json"
    config = SimpleNamespace(stash=pytest.Stash(), getoption=lambda name: report_path)
    report = {
        "status": "applied",
        "reason_code": "coverage_selection",
        "reason": "Selected",
        "selected": 2,
        "deselected": 1,
    }
    if scenario != "disabled":
        config.stash[plugin.report_key] = report.copy()
    node = SimpleNamespace(config=config, workeroutput={"skippy_cov_report": report.copy()})
    plugin.pytest_testnodedown(node, None)
    if scenario == "mismatch":
        node.workeroutput["skippy_cov_report"] = report | {"selected": 3}
    elif scenario == "missing":
        node.workeroutput = {}
    plugin.pytest_testnodedown(node, "crashed" if scenario == "crash" else None)
    plugin.pytest_sessionfinish(SimpleNamespace(config=config))
    if scenario == "disabled":
        assert not report_path.exists()
    else:
        data = json.loads(report_path.read_text())
        if scenario == "match":
            assert data == report
        else:
            assert data["status"] == "incomplete"
            assert data["reason_code"] == "worker_reports_incomplete"
            assert data["selected"] is None
            assert data["deselected"] is None


def test_worker_reporting_is_transferred_not_written(tmp_path):
    config = SimpleNamespace(stash=pytest.Stash(), workerinput={}, workeroutput={})
    config.stash[plugin.report_key] = {"selected": 1}
    plugin.write_report(config)
    plugin.pytest_sessionfinish(SimpleNamespace(config=config))
    assert config.workeroutput["skippy_cov_report"] == {"selected": 1}
    plugin.pytest_terminal_summary(SimpleNamespace(config=config))


def test_worker_module_early_exit_does_not_report(monkeypatch, capsys):
    options = {
        "skippy_cov": True,
        "skippy_cov_granularity": "module",
        "skippy_cov_keep_prefix": True,
    }
    config = SimpleNamespace(stash=pytest.Stash(), workerinput={}, args=[], getoption=options.get)
    monkeypatch.setattr(plugin, "get_diff_content", lambda _: "")
    monkeypatch.setattr(plugin, "run", lambda *args, **kwargs: set())
    with pytest.raises(pytest.exit.Exception) as error:
        plugin.pytest_configure(config)
    assert error.value.returncode == 5
    assert config.stash[plugin.report_key]["status"] == "empty"
    assert capsys.readouterr() == ("", "")


def test_module_early_exit_report_io_warning(baseline_project):
    (baseline_project.path / "changes.diff").write_text("")
    result = baseline_project.runpytest_subprocess(
        "--skippy-cov",
        "--skippy-cov-diff=changes.diff",
        "--skippy-cov-report=.",
    )
    assert result.ret == 5
    assert "cannot write report" in result.stdout.str() + result.stderr.str()
