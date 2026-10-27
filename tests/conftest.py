import difflib

import coverage
import pytest

pytest_plugins = ("pytester",)


@pytest.fixture
def coverage_file(tmp_path):
    def create(files, name=".coverage"):
        coverage_path = tmp_path / name
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        database = coverage.CoverageData(str(coverage_path))
        for filename, contexts in files.items():
            for context, lines in contexts.items():
                database.set_context(context)
                database.add_lines({str(filename): set(lines)})
        database.write()
        return coverage_path

    return create


@pytest.fixture
def baseline_project(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("COVERAGE_FILE", str(pytester.path / ".coverage"))
    pytester.makeini("[pytest]\n")
    (pytester.path / ".coveragerc").write_text("[run]\nrelative_files = true\ncore = ctrace\n")
    pytester.makepyfile(
        source="def price():\n    return 1\n",
        legacy="from unittest import TestCase\nfrom source import price\n"
        "class PriceCase(TestCase):\n"
        "    def test_price(self):\n        assert price() == 1\n"
        "    def test_unrelated(self):\n        assert True\n",
        test_other="def test_other():\n    assert True\n",
    )
    pytester.runpytest_subprocess(
        "legacy.py",
        "test_other.py",
        "--cov=source",
        "--cov-context=test",
        "--cov-report=",
    ).assert_outcomes(passed=3)
    return pytester


@pytest.fixture
def change_file(baseline_project):
    def change(filename="source.py", source="def price():\n    return 2\n"):
        path = baseline_project.path / filename
        original = path.read_text()
        path.write_text(source)
        diff = "".join(
            difflib.unified_diff(
                [line + "\n" for line in original.splitlines()],
                [line + "\n" for line in source.splitlines()],
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                n=0,
            )
        )
        (baseline_project.path / "changes.diff").write_text(diff)

    return change
