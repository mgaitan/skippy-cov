from pathlib import Path

from skippy_cov.utils import CoverageMap, FileTestCandidate


def test_load_coverage_map(coverage_file):
    coverage_path = coverage_file(
        {
            "source.py": {"test.py::test1|run": {1}, "test.py::test2|run": {1}},
        }
    )
    assert CoverageMap(coverage_path).get_tests(Path("source.py")) == [
        FileTestCandidate(path=Path("test.py"), tests={"test1", "test2"}),
    ]


def test_load_coverage_map_nested_folder(coverage_file):
    coverage_path = coverage_file({"src/source.py": {"test.py::test1|run": {1}}})
    assert CoverageMap(coverage_path).get_tests(Path("src/source.py")) == [
        FileTestCandidate(path=Path("test.py"), tests={"test1"}),
    ]
