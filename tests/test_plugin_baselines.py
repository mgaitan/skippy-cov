import pytest


@pytest.mark.parametrize("baseline", ["missing", "empty", "corrupt", "context-free", "unmapped-context"])
def test_invalid_baseline_preserves_original_selection(pytester, coverage_file, baseline):
    pytester.makepyfile("def test_pass():\n    assert True\n\ndef test_fail():\n    assert False\n")
    diff_path = pytester.path / "changes.diff"
    diff_path.write_text("")
    coverage_path = pytester.path / ".coverage"
    if baseline in {"empty", "corrupt"}:
        coverage_path.write_bytes(b"" if baseline == "empty" else b"not a coverage database")
    elif baseline in {"context-free", "unmapped-context"}:
        context = "" if baseline == "context-free" else "/separate-tests/test_source.py::test_source|run"
        coverage_path = coverage_file({"source.py": {context: {2}}})
    result = pytester.runpytest_subprocess(
        "--skippy-cov", f"--skippy-cov-diff={diff_path}", f"--skippy-cov-coverage-file={coverage_path}"
    )
    result.assert_outcomes(passed=1, failed=1)
    assert result.ret == 1
    output = result.stdout.str() + result.stderr.str()
    assert "running the original test selection" in output
    assert "INTERNALERROR" not in output
