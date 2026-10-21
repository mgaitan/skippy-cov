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
    Path("test_shared.py").write_text("def test_shared():\n    assert True\n")

    def select(
        source="def price():\n    return 2\n",
        *,
        context="test_price.py::test_price|run",
        filename="source.py",
        extra_context=None,
        original="def price():\n    return 1\n",
        context_lines=0,
        coverage_source=None,
    ):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_text(source)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                source.splitlines(keepends=True),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                n=context_lines,
            )
        )
        contexts = {context: {2}}
        contexts.setdefault("test_price.py::test_price|run", set()).add(3)
        if extra_context:
            contexts.setdefault(extra_context, set()).add(2)
        coverage_path = coverage_file({coverage_source or filename: contexts})
        return select_tests_to_run(DiffHandler(diff), CoverageMap(coverage_path))

    return select


def test_body_change_selects_complete_test_module(selection_case):
    assert selection_case() == [FileTestCandidate(Path("test_price.py"), set())]


def test_structural_change_in_separate_edit_block_selects_file_consumers(selection_case):
    original = "def price():\n    return 1\n\n\ndef shared_client():\n    return 1\n"
    source = original.replace("return 1", "return 2", 1).replace(
        "def shared_client():\n", "def shared_client():\n    assert False\n"
    )
    assert selection_case(source, original=original, context_lines=3) == [FileTestCandidate(Path("test_price.py"), set())]


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_test_module_changes_reject_shared_phase_contexts(selection_case, phase):
    with pytest.raises(SelectionError, match="shared-fixture"):
        selection_case(
            "def test_price():\n    return 2\n",
            original="def test_price():\n    return 1\n",
            filename="test_price.py",
            extra_context=f"test_price.py::test_price|{phase}",
        )


@pytest.mark.parametrize("context", ["", "test_price.py::test_price|setup", "test_price.py::test_price|teardown"])
def test_import_and_shared_fixture_contexts_require_original_selection(selection_case, context):
    with pytest.raises(SelectionError, match="shared-fixture"):
        selection_case(
            "def test_price():\n    return 2\n",
            original="def test_price():\n    return 1\n",
            filename="test_price.py",
            context=context,
        )


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_mixed_run_and_shared_fixture_contexts_require_original_selection(selection_case, phase):
    with pytest.raises(SelectionError, match="shared-fixture"):
        selection_case(
            "def test_price():\n    return 2\n",
            original="def test_price():\n    return 1\n",
            filename="test_price.py",
            extra_context=f"test_price.py::test_price|{phase}",
        )


@pytest.mark.parametrize(
    "source",
    [
        "RATE = 2\ndef price():\n    return 2\n",
        "def price(currency='ARS'):\n    return 2\n",
        "def price():\n    return 2\nRATE = 2\n",
        "def price():\n    return 1\n    print('new path')\n",
    ],
)
def test_structural_source_changes_select_known_file_consumers(selection_case, source):
    assert selection_case(source) == [FileTestCandidate(Path("test_price.py"), set())]


def test_structural_source_changes_select_all_file_consumers(selection_case):
    assert selection_case(
        "RATE = 2\ndef price():\n    return 2\n",
        extra_context="test_shared.py::test_shared|run",
    ) == [FileTestCandidate(Path("test_price.py"), set()), FileTestCandidate(Path("test_shared.py"), set())]


def test_structural_source_changes_without_consumers_require_original_selection(selection_case):
    with pytest.raises(SelectionError):
        selection_case(
            "RATE = 2\ndef price():\n    return 2\n",
            coverage_source="unrelated.py",
        )


@pytest.mark.parametrize(
    "filename",
    ["conftest.py", "__init__.py", "model.xml", "fixture.sql", "pyproject.toml", "README.md", "fixture.json"],
)
def test_non_python_and_shared_dependencies_require_original_selection(selection_case, filename):
    with pytest.raises(SelectionError, match="impact"):
        selection_case(filename=filename)
