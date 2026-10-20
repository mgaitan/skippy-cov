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
