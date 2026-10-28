import json

import pytest


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


def test_module_early_exit_report_io_warning(baseline_project):
    (baseline_project.path / "changes.diff").write_text("")
    result = baseline_project.runpytest_subprocess(
        "--skippy-cov",
        "--skippy-cov-diff=changes.diff",
        "--skippy-cov-report=.",
    )
    assert result.ret == 5
    assert "cannot write report" in result.stdout.str() + result.stderr.str()
