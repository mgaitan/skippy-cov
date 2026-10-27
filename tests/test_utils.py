from pathlib import Path

import pytest

from skippy_cov.utils import (
    FileTestCandidate,
    FilterCandidatesError,
    _fix_test_name,
    filter_by_path,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("test_file.py::test_name", ("test_file.py", "test_name")),
        ("test_file.py::ClassName::test_name", ("test_file.py", "ClassName::test_name")),
        (
            "test_file.py::test_name[param1, param2]",
            ("test_file.py", "test_name[param1, param2]"),
        ),
        (
            "test_file.py::test_name[param1, param2]|phase",
            ("test_file.py", "test_name[param1, param2]"),
        ),
        (
            "test_file.py::test_name[param|param]|phase",
            ("test_file.py", "test_name[param|param]"),
        ),
    ],
)
def test_fix_test_name(name: str, expected: str) -> None:
    assert _fix_test_name(name) == expected


def test_filter_paths():
    candidates = [
        FileTestCandidate(path=Path("/good/1"), tests=set()),
        FileTestCandidate(path=Path("/good/2"), tests=set()),
        FileTestCandidate(path=Path("/bad/1"), tests=set()),
        FileTestCandidate(path=Path("/bad/2"), tests=set()),
    ]
    expected = [
        FileTestCandidate(path=Path("1"), tests=set()),
        FileTestCandidate(path=Path("2"), tests=set()),
    ]
    assert filter_by_path(candidates, [Path("/good")], keep_prefix=False) == expected


def test_filter_paths_prefixed():
    candidates = [
        FileTestCandidate(path=Path("/good/1"), tests=set()),
        FileTestCandidate(path=Path("/good/2"), tests=set()),
        FileTestCandidate(path=Path("/bad/1"), tests=set()),
        FileTestCandidate(path=Path("/bad/2"), tests=set()),
    ]
    expected = [
        FileTestCandidate(path=Path("/good/1"), tests=set()),
        FileTestCandidate(path=Path("/good/2"), tests=set()),
    ]
    assert filter_by_path(candidates, [Path("/good")], keep_prefix=True) == expected


def test_filter_paths_prefixed_multiple_paths():
    candidates = [
        FileTestCandidate(path=Path("/good/1"), tests=set()),
        FileTestCandidate(path=Path("/good/2"), tests=set()),
        FileTestCandidate(path=Path("/better/1"), tests=set()),
        FileTestCandidate(path=Path("/better/2"), tests=set()),
        FileTestCandidate(path=Path("/bad/1"), tests=set()),
        FileTestCandidate(path=Path("/bad/2"), tests=set()),
    ]
    expected = [
        FileTestCandidate(path=Path("/good/1"), tests=set()),
        FileTestCandidate(path=Path("/good/2"), tests=set()),
        FileTestCandidate(path=Path("/better/1"), tests=set()),
        FileTestCandidate(path=Path("/better/2"), tests=set()),
    ]
    assert filter_by_path(candidates, [Path("/good"), Path("/better")], keep_prefix=True) == expected


def test_filter_paths_no_prefix_multiple_paths():
    with pytest.raises(FilterCandidatesError):
        filter_by_path([], [Path("/1"), Path("/2")], keep_prefix=False)


def test_generate_set():
    candidate = FileTestCandidate(path=Path("tests.py"), tests={"test1", "test2"})
    assert candidate.as_set() == {"tests.py::test1", "tests.py::test2"}


@pytest.mark.parametrize("context", ["::test_case|run", "tests/test_case.py::|run"])
def test_incomplete_pytest_identity_is_rejected(context):
    with pytest.raises(ValueError, match="Invalid pytest context"):
        _fix_test_name(context)


def test_relativizing_to_a_file_keeps_its_basename(tmp_path):
    path = tmp_path / "test_price.py"
    assert filter_by_path([FileTestCandidate(path, {"test_price"})], [path], keep_prefix=False) == [
        FileTestCandidate(Path("test_price.py"), {"test_price"})
    ]


def test_whole_module_selection_cannot_be_narrowed_by_later_candidates():
    from skippy_cov.utils import merge_candidates

    path = Path("test_price.py")
    assert merge_candidates([FileTestCandidate(path, set()), FileTestCandidate(path, {"test_price"})]) == [
        FileTestCandidate(path, set())
    ]
