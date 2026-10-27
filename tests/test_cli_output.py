import json
import shlex
from pathlib import Path

from skippy_cov.__main__ import Format, run
from skippy_cov.utils import FileTestCandidate, filter_by_path


def test_json_output_unions_candidates_for_same_module(coverage_file, mocker, capsys):
    baseline_path = coverage_file({"source.py": {"tests/test_shared.py::test_one|run": {2}}})
    mocker.patch(
        "skippy_cov.__main__.select_tests_to_run",
        return_value=[
            FileTestCandidate(Path("tests/test_shared.py"), {"test_z"}),
            FileTestCandidate(Path("tests/test_shared.py"), {"test_a"}),
        ],
    )
    run("", baseline_path, None, True, Format.json, display=True)
    assert json.loads(capsys.readouterr().out) == {"tests/test_shared.py": ["test_a", "test_z"]}


def test_output_order_is_deterministic(coverage_file, mocker, capsys):
    baseline_path = coverage_file({"source.py": {"tests/test_shared.py::test_one|run": {2}}})
    mocker.patch(
        "skippy_cov.__main__.select_tests_to_run",
        return_value=[
            FileTestCandidate(Path("tests/test_z.py"), {"test_z", "test_a"}),
            FileTestCandidate(Path("tests/test_a.py"), {"test_one"}),
        ],
    )
    run("", baseline_path, None, True, Format.lines, display=True)
    assert capsys.readouterr().out.splitlines() == [
        "tests/test_a.py::test_one",
        "tests/test_z.py::test_a",
        "tests/test_z.py::test_z",
    ]


def test_whole_module_selection_supersedes_individual_node_ids(coverage_file, mocker, capsys):
    baseline_path = coverage_file({"source.py": {"tests/test_shared.py::test_one|run": {2}}})
    mocker.patch(
        "skippy_cov.__main__.select_tests_to_run",
        return_value=[
            FileTestCandidate(Path("tests/test shared.py"), {"test_one"}),
            FileTestCandidate(Path("tests/test shared.py"), set()),
        ],
    )
    assert run("", baseline_path, None, True, display=True) == {"tests/test shared.py"}
    assert shlex.split(capsys.readouterr().out) == ["tests/test shared.py"]


def test_path_filter_does_not_mutate_candidates_or_duplicate_overlapping_scopes():
    candidate = FileTestCandidate(Path("/tests/server/test_price.py"), {"test_price"})
    filtered = filter_by_path([candidate], [Path("/tests")], keep_prefix=False)
    assert filtered[0].path == Path("server/test_price.py")
    assert candidate.path == Path("/tests/server/test_price.py")
    assert filter_by_path([candidate], [Path("/tests"), Path("/tests/server")]) == [candidate]


def test_cli_path_scope_is_applied_before_rendering(coverage_file, mocker, capsys):
    baseline = coverage_file({"source.py": {"tests/test_price.py::test_price|run": {2}}})
    mocker.patch(
        "skippy_cov.__main__.select_tests_to_run",
        return_value=[
            FileTestCandidate(Path("tests/test_price.py"), set()),
            FileTestCandidate(Path("other.py"), set()),
        ],
    )
    assert run("", baseline, [Path("tests")], False, Format.lines, display=True) == {"test_price.py"}
    assert capsys.readouterr().out == "test_price.py\n"


def test_file_level_custom_pytest_item_preserves_module_target(coverage_file, mocker):
    from skippy_cov.utils import CollectedTest

    path = Path("tests/test_custom.py")
    baseline = coverage_file({"source.py": {"tests/test_source.py::test_source|run": {2}}})
    mocker.patch("skippy_cov.__main__.collect_current_tests", return_value=[CollectedTest(path, "")])
    mocker.patch("skippy_cov.__main__.select_tests_to_run", return_value=[FileTestCandidate(path, set())])
    assert run("", baseline, None, True, collect=True) == {"tests/test_custom.py"}
