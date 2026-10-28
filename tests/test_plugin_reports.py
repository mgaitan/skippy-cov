import json
import subprocess
from types import SimpleNamespace

import pytest

from skippy_cov import plugin


@pytest.fixture
def changed_project(baseline_project, change_file):
    change_file()
    return baseline_project


@pytest.fixture
def xdist_project(baseline_project, change_file):
    for args in (
        ["init", "-q"],
        ["add", "*.py"],
        ["-c", "user.name=Tests", "-c", "user.email=tests@example.com", "commit", "-qm", "baseline"],
    ):
        subprocess.run(["git", *args], cwd=baseline_project.path, check=True)
    change_file(source="def price():\n    return 1 + 0\n")
    return baseline_project


def run_report(project, mode, *arguments):
    return project.runpytest_subprocess(
        f"--skippy-cov-granularity={mode}",
        "--skippy-cov-diff=changes.diff",
        "--skippy-cov-report=report.json",
        "legacy.py",
        "test_other.py",
        *arguments,
    )


def read_report(project, result, status):
    data = json.loads((project.path / "report.json").read_text())
    assert data["status"] == status
    output = result.stdout.str() + result.stderr.str()
    assert output.count(f"skippy-cov: {status} ({data['reason_code']})") == 1
    return data


def assert_report_metadata(data, mode, coverage_file=".coverage"):
    assert data["schema_version"] == 1
    assert data["granularity"] == mode
    assert data["baseline"] == {
        "coverage_file": coverage_file,
        "source_root": None,
        "base_ref": None,
    }


def test_module_selection_report_applied(changed_project):
    project = changed_project
    result = run_report(project, "module", "--skippy-cov")
    data = read_report(project, result, "applied")
    assert_report_metadata(data, "module")
    result.assert_outcomes(failed=1, passed=1)
    assert data["selected"] == 2
    assert data["deselected"] is None


def test_module_selection_report_falls_back_without_coverage(changed_project):
    project = changed_project
    result = run_report(project, "module", "--skippy-cov", "--skippy-cov-coverage-file=missing")
    data = read_report(project, result, "fallback")
    assert_report_metadata(data, "module", "missing")
    result.assert_outcomes(failed=1, passed=2)
    assert data["reason_code"] == "selection_error"
    assert data["selected"] == 3
    assert data["deselected"] == 0
    assert data["reason"]


def test_module_selection_report_empty_diff(changed_project):
    project = changed_project
    (project.path / "changes.diff").write_text("")
    result = run_report(project, "module", "--skippy-cov")
    data = read_report(project, result, "empty")
    assert_report_metadata(data, "module")
    assert result.ret == 5
    assert data["selected"] == 0
    assert data["deselected"] is None


def test_module_selection_report_disabled(changed_project):
    project = changed_project
    result = run_report(project, "module")
    result.assert_outcomes(failed=1, passed=2)
    assert not (project.path / "report.json").exists()
    assert "skippy-cov:" not in result.stdout.str() + result.stderr.str()


def test_module_selection_report_write_error(changed_project):
    project = changed_project
    (project.path / "report.json").mkdir()
    result = run_report(project, "module", "--skippy-cov")
    assert result.ret == 1
    assert "cannot write report" in result.stdout.str() + result.stderr.str()


def test_test_selection_report_falls_back_without_base_source(changed_project):
    project = changed_project
    result = run_report(project, "test", "--skippy-cov")
    data = read_report(project, result, "fallback")
    assert_report_metadata(data, "test")
    result.assert_outcomes(failed=1, passed=2)
    assert data["reason_code"] == "selection_error"
    assert data["selected"] == 3
    assert data["deselected"] == 0
    assert data["reason"]


def test_test_selection_report_falls_back_without_coverage(changed_project):
    project = changed_project
    result = run_report(project, "test", "--skippy-cov", "--skippy-cov-coverage-file=missing")
    data = read_report(project, result, "fallback")
    assert_report_metadata(data, "test", "missing")
    result.assert_outcomes(failed=1, passed=2)
    assert data["reason_code"] == "selection_error"
    assert data["selected"] == 3
    assert data["deselected"] == 0
    assert data["reason"]


def test_test_selection_report_empty_diff(changed_project):
    project = changed_project
    (project.path / "changes.diff").write_text("")
    result = run_report(project, "test", "--skippy-cov")
    data = read_report(project, result, "empty")
    assert_report_metadata(data, "test")
    assert result.ret == 5
    assert data["selected"] == 0
    assert data["deselected"] == 3


def test_test_selection_report_disabled(changed_project):
    project = changed_project
    result = run_report(project, "test")
    result.assert_outcomes(failed=1, passed=2)
    assert not (project.path / "report.json").exists()
    assert "skippy-cov:" not in result.stdout.str() + result.stderr.str()


def test_test_selection_report_write_error(changed_project):
    project = changed_project
    (project.path / "report.json").mkdir()
    result = run_report(project, "test", "--skippy-cov")
    assert result.ret == 1
    assert "cannot write report" in result.stdout.str() + result.stderr.str()


def test_xdist_module_selection_report_applied(xdist_project):
    project = xdist_project
    result = run_report(project, "module", "--skippy-cov", "-n2", "--skippy-cov-base-ref=HEAD")
    data = read_report(project, result, "applied")
    result.assert_outcomes(passed=2)
    assert data["selected"] == 2
    assert data["deselected"] is None


def test_xdist_module_selection_report_fallback(xdist_project):
    project = xdist_project
    result = run_report(
        project, "module", "--skippy-cov", "-n2", "--skippy-cov-base-ref=HEAD", "--skippy-cov-coverage-file=missing"
    )
    data = read_report(project, result, "fallback")
    result.assert_outcomes(passed=3)
    assert data["selected"] == 3
    assert data["deselected"] == 0


def test_xdist_module_selection_report_empty(xdist_project):
    project = xdist_project
    (project.path / "changes.diff").write_text("")
    result = run_report(project, "module", "--skippy-cov", "-n2", "--skippy-cov-base-ref=HEAD")
    data = read_report(project, result, "empty")
    assert result.ret == 5
    assert data["selected"] == 0
    assert data["deselected"] is None


def test_xdist_test_selection_report_applied(xdist_project):
    project = xdist_project
    result = run_report(project, "test", "--skippy-cov", "-n2", "--skippy-cov-base-ref=HEAD")
    data = read_report(project, result, "applied")
    result.assert_outcomes(passed=1)
    assert data["selected"] == 1
    assert data["deselected"] == 2


def test_xdist_test_selection_report_fallback(xdist_project):
    project = xdist_project
    result = run_report(
        project, "test", "--skippy-cov", "-n2", "--skippy-cov-base-ref=HEAD", "--skippy-cov-coverage-file=missing"
    )
    data = read_report(project, result, "fallback")
    result.assert_outcomes(passed=3)
    assert data["selected"] == 3
    assert data["deselected"] == 0


def test_xdist_test_selection_report_empty(xdist_project):
    project = xdist_project
    (project.path / "changes.diff").write_text("")
    result = run_report(project, "test", "--skippy-cov", "-n2", "--skippy-cov-base-ref=HEAD")
    data = read_report(project, result, "empty")
    assert result.ret == 5
    assert data["selected"] == 0
    assert data["deselected"] == 3


@pytest.fixture
def worker_report():
    return {
        "status": "applied",
        "reason_code": "coverage_selection",
        "reason": "Selected",
        "selected": 2,
        "deselected": 1,
    }


@pytest.fixture
def reporting_node(tmp_path, worker_report):
    report_path = tmp_path / "report.json"
    config = SimpleNamespace(stash=pytest.Stash(), getoption=lambda name: report_path)
    config.stash[plugin.report_key] = worker_report.copy()
    node = SimpleNamespace(config=config, workeroutput={"skippy_cov_report": worker_report.copy()})
    plugin.pytest_testnodedown(node, None)
    return node


def finish_worker_report(node, tmp_path):
    plugin.pytest_sessionfinish(SimpleNamespace(config=node.config))
    return json.loads((tmp_path / "report.json").read_text())


def assert_incomplete_worker_report(data):
    assert data["status"] == "incomplete"
    assert data["reason_code"] == "worker_reports_incomplete"
    assert data["selected"] is None
    assert data["deselected"] is None


def test_worker_report_aggregation_match(reporting_node, tmp_path, worker_report):
    plugin.pytest_testnodedown(reporting_node, None)
    assert finish_worker_report(reporting_node, tmp_path) == worker_report


def test_worker_report_aggregation_mismatch(reporting_node, tmp_path, worker_report):
    reporting_node.workeroutput["skippy_cov_report"] = worker_report | {"selected": 3}
    plugin.pytest_testnodedown(reporting_node, None)
    assert_incomplete_worker_report(finish_worker_report(reporting_node, tmp_path))


def test_worker_report_aggregation_missing(reporting_node, tmp_path):
    reporting_node.workeroutput = {}
    plugin.pytest_testnodedown(reporting_node, None)
    assert_incomplete_worker_report(finish_worker_report(reporting_node, tmp_path))


def test_worker_report_aggregation_crash(reporting_node, tmp_path):
    plugin.pytest_testnodedown(reporting_node, "crashed")
    assert_incomplete_worker_report(finish_worker_report(reporting_node, tmp_path))


def test_worker_report_aggregation_disabled(tmp_path, worker_report):
    report_path = tmp_path / "report.json"
    config = SimpleNamespace(stash=pytest.Stash(), getoption=lambda name: report_path)
    node = SimpleNamespace(config=config, workeroutput={"skippy_cov_report": worker_report.copy()})
    plugin.pytest_testnodedown(node, None)
    plugin.pytest_testnodedown(node, None)
    plugin.pytest_sessionfinish(SimpleNamespace(config=config))
    assert not report_path.exists()


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
