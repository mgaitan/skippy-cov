from pathlib import Path

import pytest

from skippy_cov.utils import CoverageMap, FileTestCandidate

TEST_CONTEXT = "tests/test_source.py::test_source|run"


def test_coverage_path_preserves_directory(coverage_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_path = coverage_file({"source.py": {TEST_CONTEXT: {2}}}, name="baseline/.coverage")
    coverage_file({"other.py": {"tests/test_other.py::test_other|run": {1}}})
    assert CoverageMap(baseline_path).get_tests(Path("source.py")) == [
        FileTestCandidate(Path("tests/test_source.py"), {"test_source"}),
    ]


def test_absolute_source_paths_match_relative_diff_paths(coverage_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_path = coverage_file({str(tmp_path / "source.py"): {TEST_CONTEXT: {2}}})
    assert CoverageMap(baseline_path).get_tests(Path("source.py"))


def test_relative_source_paths_match_absolute_requests(coverage_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_path = coverage_file({"source.py": {TEST_CONTEXT: {2}}})
    assert CoverageMap(baseline_path).get_tests(tmp_path / "source.py")


def test_source_root_maps_docker_paths_explicitly(coverage_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_path = coverage_file({"/fierro/source.py": {TEST_CONTEXT: {2}}})
    assert CoverageMap(baseline_path, source_root=Path("/fierro")).get_tests(Path("source.py"))


def test_wrong_source_root_is_not_guessed_from_suffix(coverage_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_path = coverage_file({"/other/source.py": {TEST_CONTEXT: {2}}})
    with pytest.raises(ValueError, match="root"):
        CoverageMap(baseline_path).get_tests(Path("source.py"))


@pytest.mark.parametrize("content", [None, b"", b"not a coverage database"])
def test_missing_empty_or_corrupt_coverage_is_rejected(tmp_path, content):
    coverage_path = tmp_path / ".coverage"
    if content is not None:
        coverage_path.write_bytes(content)
    with pytest.raises(ValueError, match=r"coverage|Coverage"):
        CoverageMap(coverage_path)


@pytest.mark.parametrize("context", ["", "static-context"])
def test_coverage_without_pytest_contexts_is_rejected(coverage_file, context):
    baseline_path = coverage_file({"source.py": {context: {1}}})
    with pytest.raises(ValueError, match="context"):
        CoverageMap(baseline_path)


def test_parameter_id_with_pipe_is_preserved_without_phase(coverage_file):
    baseline_path = coverage_file({"source.py": {"tests/test_source.py::test_source[a|b]": {2}}})
    assert CoverageMap(baseline_path).get_tests(Path("source.py")) == [
        FileTestCandidate(Path("tests/test_source.py"), {"test_source[a|b]"}),
    ]


def test_relative_and_absolute_records_for_same_file_are_combined(coverage_file, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline_path = coverage_file(
        {
            "source.py": {TEST_CONTEXT: {2}},
            str(tmp_path / "source.py"): {"tests/test_source.py::test_other|run": {2}},
        }
    )
    assert CoverageMap(baseline_path).get_tests(Path("source.py")) == [
        FileTestCandidate(Path("tests/test_source.py"), {"test_source", "test_other"}),
    ]
