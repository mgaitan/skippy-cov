import difflib
from pathlib import Path

import pytest

from skippy_cov import SelectionError, select_tests_to_run
from skippy_cov.diff_handler import DiffHandler
from skippy_cov.utils import CoverageMap, FileTestCandidate


@pytest.fixture
def selection_case(tmp_path, monkeypatch, coverage_file):
    monkeypatch.chdir(tmp_path)
    Path("test_price.py").write_text("def test_price():\n    assert True\n")

    def select(source="def price():\n    return 2\n", *, context="test_price.py::test_price|run", filename="source.py"):
        original = "def price():\n    return 1\n"
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_text(source)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                source.splitlines(keepends=True),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                n=0,
            )
        )
        contexts = {context: {2}}
        contexts.setdefault("test_price.py::test_price|run", set()).add(3)
        coverage_path = coverage_file({filename: contexts})
        return select_tests_to_run(DiffHandler(diff), CoverageMap(coverage_path))

    return select


def test_body_change_selects_complete_test_module(selection_case):
    assert selection_case() == [FileTestCandidate(Path("test_price.py"), set())]


@pytest.mark.parametrize("context", ["", "test_price.py::test_price|setup", "test_price.py::test_price|teardown"])
def test_import_and_shared_fixture_contexts_require_original_selection(selection_case, context):
    with pytest.raises(SelectionError, match="shared-fixture"):
        selection_case(context=context)


@pytest.mark.parametrize(
    "source",
    [
        "RATE = 2\ndef price():\n    return 2\n",
        "def price(currency='ARS'):\n    return 2\n",
        "def price():\n    return 2\nRATE = 2\n",
        "def price():\n    return 1\n    print('new path')\n",
    ],
)
def test_import_declaration_and_addition_only_changes_require_original_selection(selection_case, source):
    with pytest.raises(SelectionError, match=r"imports|declarations"):
        selection_case(source)


@pytest.mark.parametrize("filename", ["conftest.py", "__init__.py", "model.xml", "fixture.sql", "pyproject.toml"])
def test_non_python_and_shared_dependencies_require_original_selection(selection_case, filename):
    with pytest.raises(SelectionError, match="impact"):
        selection_case(filename=filename)
