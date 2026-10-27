import json
import subprocess
import sys
from pathlib import Path

import pytest

from skippy_cov import Granularity, SelectionError, select_tests_to_run
from skippy_cov.__main__ import collect_current_tests, get_base_ref, run
from skippy_cov.diff_handler import DiffHandler, DiffHandlerError
from skippy_cov.utils import CoverageMap


def git(project, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=project.path, text=True).strip()


def baseline(project, *, passed=4):
    git(project, "add", "--", "*.py", "tox.ini", ".coveragerc")
    git(project, "commit", "-qm", "baseline")
    revision = git(project, "rev-parse", "HEAD")
    project.runpytest_subprocess(
        ".", "--cov=source", "--cov=test_helpers", "--cov=test_external", "--cov-report=", "--cov-context=test"
    ).assert_outcomes(passed=passed)
    return revision


@pytest.fixture
def scope_project(baseline_project):
    project = baseline_project
    project.makepyfile(
        source="def price(discount=False):\n    if discount:\n        return 0\n    return 1\n\n"
        "def other():\n    return 10\n",
        test_helpers="from source import price, other\n"
        "def test_price():\n    assert price() == 1\n"
        "def test_other():\n    assert other() == 10\n",
        test_external="from source import price\ndef test_external():\n    assert price() >= 1\n",
    )
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.email", "tests@example.com")
    git(project, "config", "user.name", "Skippy tests")
    return project, baseline(project)


def prepare_diff(project, revision):
    git(project, "add", "--intent-to-add", "--", "*.py")
    patch = subprocess.check_output(["git", "diff", revision, "--"], cwd=project.path, text=True)
    (project.path / "changes.diff").write_text(patch)
    return patch


def run_scope(project, revision, *targets, granularity="test"):
    prepare_diff(project, revision)
    return project.runpytest_subprocess(
        "--skippy-cov",
        "--skippy-cov-diff=changes.diff",
        f"--skippy-cov-base-ref={revision}",
        f"--skippy-cov-granularity={granularity}",
        *(targets or (".",)),
    )


def change_price(project):
    path = project.path / "source.py"
    path.write_text(path.read_text().replace("return 1\n", "return 2\n"))


def test_fine_module_and_full_runs_keep_the_same_failure(scope_project):
    project, revision = scope_project
    change_price(project)

    run_scope(project, revision).assert_outcomes(failed=1, passed=1, deselected=2)
    run_scope(project, revision, granularity="module").assert_outcomes(failed=1, passed=2)
    project.runpytest_subprocess(".").assert_outcomes(failed=1, passed=3)


@pytest.mark.parametrize(
    "updated",
    [
        "def price(discount=False):\n    if discount:\n        return 99\n    return 1\n\ndef other():\n    return 10\n",
        "def price(discount=False):\n    if discount:\n        raise ValueError('new branch')\n        return 0\n    return 1\n\ndef other():\n    return 10\n",
        "# Shift every definition\n\n\ndef price(discount=False):\n    if discount:\n        return 0\n    return 2\n\ndef other():\n    return 10\n",
        "def price(discount=False):\n    return 1\n\ndef other():\n    return 10\n",
    ],
    ids=["uncovered-replacement", "uncovered-insertion", "line-shift", "deletion-only"],
)
def test_selection_uses_whole_baseline_function_ranges(scope_project, updated):
    project, revision = scope_project
    (project.path / "source.py").write_text(updated)
    patch = prepare_diff(project, revision)

    assert run(patch, project.path / ".coverage", None, True, granularity=Granularity.test, base_ref=revision) == {
        "test_helpers.py::test_price",
        "test_external.py::test_external",
    }


@pytest.mark.parametrize("addition", ["new-function", "new-module", "changed-test"])
def test_new_and_changed_tests_are_included_without_coverage_relationship(scope_project, addition):
    project, revision = scope_project
    change_price(project)
    if addition == "new-module":
        project.makepyfile(test_new="def test_new():\n    assert False\n")
    else:
        path = project.path / "test_other.py"
        original = path.read_text()
        if addition == "new-function":
            path.write_text(original + "\ndef test_new():\n    assert False\n")
        else:
            path.write_text(original.replace("assert True", "assert False"))

    result = run_scope(project, revision)
    assert result.ret == 1
    assert result.parseoutcomes()["failed"] == 2
    assert "test_new" in result.stdout.str() if addition != "changed-test" else "test_other" in result.stdout.str()


def test_new_parameter_cases_are_included_even_if_not_in_baseline(scope_project):
    project, revision = scope_project
    path = project.path / "test_other.py"
    path.write_text(
        "import pytest\n@pytest.mark.parametrize('amount', [1, 2])\ndef test_other(amount):\n    assert amount < 2\n"
    )

    # This file has no coverage of production code, and no recorded contexts.
    result = run_scope(project, revision)
    result.assert_outcomes(failed=1, passed=4)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


@pytest.mark.parametrize("targets", [("test_helpers.py::test_other",), ("-k", "other"), ("--rootdir=tests",)])
def test_fine_mode_respects_current_pytest_scopes(scope_project, targets):
    project, revision = scope_project
    change_price(project)
    if targets == ("--rootdir=tests",):
        (project.path / "tests").mkdir()
        run_scope(project, revision, *targets, "test_helpers.py").assert_outcomes(failed=1, deselected=1)
    else:
        result = run_scope(project, revision, *targets)
        assert result.ret == 5
        assert "failed" not in result.parseoutcomes()


@pytest.mark.parametrize("change", ["new-helper", "class-attribute", "signature"])
def test_structural_changes_keep_the_original_selection(scope_project, change):
    project, revision = scope_project
    path = project.path / "source.py"
    original = path.read_text()
    if change == "new-helper":
        updated = original + "\ndef new_helper():\n    return 2\n"
    elif change == "class-attribute":
        updated = "class Options:\n    rate = 2\n\n" + original
    else:
        updated = original.replace("discount=False", "discount=True")
    path.write_text(updated)

    result = run_scope(project, revision)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()
    assert not result.parseoutcomes().get("deselected")


def test_cli_test_granularity_emits_individual_json_targets(scope_project):
    project, revision = scope_project
    change_price(project)
    prepare_diff(project, revision)
    coverage_before = (project.path / ".coverage").read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "skippy_cov",
            "--diff",
            "changes.diff",
            "--base-ref",
            revision,
            "--granularity",
            "test",
            "--format",
            "json",
        ],
        cwd=project.path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"test_helpers.py": ["test_price"], "test_external.py": ["test_external"]}
    assert (project.path / ".coverage").read_bytes() == coverage_before


def test_patch_without_base_source_is_not_narrowed(scope_project):
    project, revision = scope_project
    change_price(project)
    patch = prepare_diff(project, revision)
    with pytest.raises(DiffHandlerError, match="baseline revision"):
        select_tests_to_run(DiffHandler(patch), CoverageMap(project.path / ".coverage"), granularity=Granularity.test)


def test_base_ref_matches_the_old_revision_of_reference_diffs(scope_project):
    project, revision = scope_project
    change_price(project)
    git(project, "add", "source.py")
    git(project, "commit", "-qm", "change")
    assert get_base_ref(f"{revision}..HEAD") == revision
    assert get_base_ref(revision) == revision
    with pytest.raises(DiffHandlerError, match="does not match"):
        get_base_ref(f"{revision}..HEAD", "HEAD")


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_full_scope_shared_contexts_cannot_be_narrowed(scope_project, coverage_file, phase):
    project, revision = scope_project
    change_price(project)
    patch = prepare_diff(project, revision)
    coverage_path = coverage_file(
        {"source.py": {"test_helpers.py::test_price|run": {4}, f"test_external.py::test_external|{phase}": {2, 3}}}
    )
    with pytest.raises(SelectionError, match="shared-fixture"):
        select_tests_to_run(
            DiffHandler(patch, base_ref=revision), CoverageMap(coverage_path), granularity=Granularity.test
        )


@pytest.mark.parametrize(
    "source",
    [
        "async def price():\n    return 1\n\ndef other():\n    return 10\n",
        "def price():\n    def inner():\n        return 1\n    return inner()\n\ndef other():\n    return 10\n",
        "class Quote:\n    def price(self):\n        return 1\n    def other(self):\n        return 10\n"
        "price = Quote().price\nother = Quote().other\n",
    ],
    ids=["async", "nested", "method"],
)
def test_async_nested_and_method_consumers_are_individual(scope_project, source):
    project, _ = scope_project
    project.makepyfile(source=source)
    if source.startswith("async"):
        for filename in ("test_helpers.py", "test_external.py"):
            path = project.path / filename
            path.write_text(
                "import asyncio\n" + path.read_text().replace("assert price()", "assert asyncio.run(price())")
            )
    revision = baseline(project)
    change_price(project)

    run_scope(project, revision).assert_outcomes(failed=1, passed=1, deselected=2)


def test_new_parameter_case_in_a_known_test_module_runs_without_source_impact(scope_project):
    project, _ = scope_project
    path = project.path / "test_helpers.py"
    path.write_text(
        "import pytest\n"
        + path.read_text().replace(
            "def test_price():\n    assert price() == 1",
            "@pytest.mark.parametrize('amount', [1])\ndef test_price(amount):\n    assert price() == amount",
        )
    )
    revision = baseline(project)
    path.write_text(path.read_text().replace("[1]", "[1, 2]"))

    result = run_scope(project, revision)
    result.assert_outcomes(failed=1, passed=4)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


@pytest.mark.parametrize("structural", [False, True], ids=["body", "declaration"])
def test_session_fixture_failure_remains_visible_in_fine_mode(scope_project, structural):
    project, _ = scope_project
    if structural:
        path = project.path / "source.py"
        path.write_text("RATE = 1\n" + path.read_text().replace("return 1\n", "return RATE\n"))
    project.makeconftest(
        "import pytest\nfrom source import price\n"
        "@pytest.fixture(scope='session')\ndef shared_price():\n    return price()\n"
    )
    for filename, test_name in (("test_helpers.py", "test_price"), ("test_external.py", "test_external")):
        path = project.path / filename
        path.write_text(
            path.read_text()
            .replace(f"def {test_name}():", f"def {test_name}(shared_price):")
            .replace("assert price()", "assert shared_price")
        )
    revision = baseline(project)
    if structural:
        path = project.path / "source.py"
        path.write_text(path.read_text().replace("RATE = 1", "RATE = 2"))
    else:
        change_price(project)

    result = run_scope(project, revision)
    result.assert_outcomes(failed=1, passed=3)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


def test_mark_expression_is_preserved_in_fine_mode(scope_project):
    project, _ = scope_project
    project.makeini("[pytest]\nmarkers = affected\n")
    path = project.path / "test_helpers.py"
    path.write_text(
        "import pytest\n" + path.read_text().replace("def test_price():", "@pytest.mark.affected\ndef test_price():")
    )
    revision = baseline(project)
    change_price(project)

    run_scope(project, revision, "-m", "affected").assert_outcomes(failed=1, deselected=3)


@pytest.mark.parametrize("edit", ["comment-like-text", "blank-line"])
def test_multiline_string_content_is_not_ignored_as_spacing_or_comments(scope_project, edit):
    project, _ = scope_project
    path = project.path / "source.py"
    path.write_text(
        path.read_text().replace("    return 1\n", "    message = '''start\n# first\n\nend'''\n    return 1\n")
    )
    revision = baseline(project)
    source = path.read_text()
    path.write_text(
        source.replace("# first", "# second") if edit == "comment-like-text" else source.replace("\n\nend", "\nend")
    )
    patch = prepare_diff(project, revision)

    assert run(patch, project.path / ".coverage", None, True, granularity=Granularity.test, base_ref=revision) == {
        "test_helpers.py::test_price",
        "test_external.py::test_external",
    }


@pytest.mark.parametrize("cli", [False, True], ids=["plugin", "cli"])
def test_new_inherited_tests_are_always_included(scope_project, cli):
    project, _ = scope_project
    project.makepyfile(
        test_helpers="from unittest import TestCase\nfrom source import price, other\n"
        "class PriceCase(TestCase):\n"
        "    def test_price(self):\n        assert price() == 1\n"
        "    def test_other(self):\n        assert other() == 10\n",
        test_child="import test_helpers\nclass ChildCase(test_helpers.PriceCase):\n    pass\n",
    )
    revision = baseline(project, passed=6)
    path = project.path / "test_helpers.py"
    path.write_text(path.read_text() + "\n    def test_new(self):\n        assert False\n")
    patch = prepare_diff(project, revision)
    if cli:
        selected = run(
            patch, project.path / ".coverage", None, True, granularity=Granularity.test, base_ref=revision, collect=True
        )
        assert "test_child.py::ChildCase::test_new" in selected
        result = project.runpytest_subprocess(*sorted(selected))
        result.assert_outcomes(failed=2, passed=4)
    else:
        run_scope(project, revision).assert_outcomes(failed=2, passed=4, deselected=2)


@pytest.mark.parametrize("cli", [False, True], ids=["plugin", "cli"])
def test_broad_python_files_does_not_misclassify_source_as_tests(scope_project, cli):
    project, _ = scope_project
    project.makeini("[pytest]\npython_files = *.py\n")
    revision = baseline(project, passed=6)
    change_price(project)
    patch = prepare_diff(project, revision)
    if cli:
        selected = run(
            patch, project.path / ".coverage", None, True, granularity=Granularity.test, base_ref=revision, collect=True
        )
        assert selected == {
            "legacy.py::PriceCase::test_price",
            "test_helpers.py::test_price",
            "test_external.py::test_external",
        }
    else:
        result = run_scope(project, revision)
        result.assert_outcomes(failed=2, passed=1, deselected=3)
        assert "running the original test selection" not in result.stdout.str() + result.stderr.str()


@pytest.mark.parametrize("kind", ["source", "test"])
def test_new_files_with_broad_patterns_are_classified_by_pytest(scope_project, kind):
    project, _ = scope_project
    project.makeini("[pytest]\npython_files = *.py\n")
    revision = baseline(project, passed=6)
    if kind == "source":
        project.makepyfile(new_helpers="def new_helper():\n    return 1\n")
        result = run_scope(project, revision)
        result.assert_outcomes(passed=6)
        assert "running the original test selection" in result.stdout.str() + result.stderr.str()
    else:
        project.makepyfile(extra_checks="def test_new():\n    assert False\n")
        run_scope(project, revision).assert_outcomes(failed=1, deselected=6)


@pytest.mark.parametrize("granularity", ["module", "test"])
def test_constant_change_keeps_import_time_consumers(scope_project, granularity):
    project, _ = scope_project
    path = project.path / "source.py"
    path.write_text("RATE = 1\n" + path.read_text())
    project.makepyfile(test_imported="from source import RATE\ndef test_rate():\n    assert RATE == 1\n")
    revision = baseline(project, passed=5)
    path.write_text(path.read_text().replace("RATE = 1", "RATE = 2"))

    result = run_scope(project, revision, granularity=granularity)
    result.assert_outcomes(failed=1, passed=4)
    patch = prepare_diff(project, revision)
    with pytest.raises(SelectionError):
        run(patch, project.path / ".coverage", None, True, granularity=Granularity(granularity), base_ref=revision)


def test_cli_collection_respects_configured_testpaths(scope_project):
    project, _ = scope_project
    project.makeini("[pytest]\ntestpaths = selected\n")
    directory = project.path / "selected"
    directory.mkdir()
    (directory / "test_selected.py").write_text(
        "from source import price\ndef test_selected():\n    assert price() == 1\n"
    )
    project.makepyfile(test_outside="raise RuntimeError('outside configured testpaths')\n")

    tests = collect_current_tests(None)
    assert {test.path.as_posix() for test in tests} == {"selected/test_selected.py"}
    assert {test.path.as_posix() for test in collect_current_tests([Path("test_other.py")])} == {"test_other.py"}


@pytest.mark.parametrize("cli", [False, True], ids=["plugin", "cli"])
@pytest.mark.parametrize(
    "source",
    [
        "from functools import lru_cache\n@lru_cache\ndef price():\n    return 1\n",
        "from functools import lru_cache\nclass Quote:\n    @lru_cache\n    def price(self):\n        return 1\n"
        "price = Quote().price\n",
        "from functools import lru_cache\ndef cached(cls):\n    cls.price = lru_cache(cls.price)\n    return cls\n"
        "@cached\nclass Quote:\n    def price(self):\n        return 1\nprice = Quote().price\n",
    ],
    ids=["function", "method", "class"],
)
def test_decorated_scopes_keep_cached_consumers(scope_project, cli, source):
    project, _ = scope_project
    project.makepyfile(
        source=source,
        test_helpers="from source import price\ndef test_first():\n    assert price() >= 1\n"
        "def test_second():\n    assert price() == 1\n",
        test_external="def test_external():\n    assert True\n",
    )
    path = project.path / "source.py"
    path.write_text(path.read_text() + "\n")
    revision = baseline(project)
    change_price(project)
    if cli:
        patch = prepare_diff(project, revision)
        with pytest.raises(SelectionError, match="Decorated scope"):
            run(patch, project.path / ".coverage", None, True, granularity=Granularity.test, base_ref=revision)
    else:
        run_scope(project, revision).assert_outcomes(failed=1, passed=3)
        project.runpytest_subprocess("--skippy-cov", "--skippy-cov-diff=changes.diff", ".").assert_outcomes(
            failed=1, passed=1
        )


@pytest.mark.parametrize("granularity", ["module", "test"])
@pytest.mark.parametrize("edit", ["replace", "delete"])
def test_test_module_string_edits_are_not_treated_as_comments(scope_project, granularity, edit):
    project, _ = scope_project
    project.makepyfile(
        test_data="DATA = '''first\n# value\nlast'''\ndef test_data():\n    assert True\n",
        test_imported="from test_data import DATA\ndef test_data():\n    assert '# value' in DATA\n",
    )
    path = project.path / "test_data.py"
    path.write_text(path.read_text() + "\n")
    revision = baseline(project, passed=6)
    path.write_text(
        path.read_text().replace("# value", "# changed")
        if edit == "replace"
        else path.read_text().replace("# value\n", "")
    )
    result = run_scope(project, revision, granularity=granularity)
    assert result.parseoutcomes().get("failed") == 1


def test_cli_collection_errors_are_not_an_empty_selection(pytester):
    pytester.makepyfile(test_broken="raise RuntimeError('cannot collect this module')\n")
    with pytest.raises(ValueError, match="cannot collect this module"):
        collect_current_tests(None)


@pytest.mark.parametrize(
    ("original", "updated", "message"),
    [
        (
            "def price(): return 1\ndef price(): return 1\ndef other(): return 10\n",
            "def price(): return 2\ndef price(): return 2\ndef other(): return 10\n",
            "Ambiguous repeated scope",
        ),
        (
            "def price(discount=False): return 1\ndef other(): return 10\n",
            "def price(discount=True): return 1\ndef other(): return 10\n",
            "structurally changed scope",
        ),
    ],
    ids=["duplicate-definition", "single-line-signature"],
)
def test_ambiguous_source_scopes_require_original_selection(scope_project, original, updated, message):
    project, _ = scope_project
    path = project.path / "source.py"
    path.write_text(original)
    revision = baseline(project)
    path.write_text(updated)
    patch = prepare_diff(project, revision)
    with pytest.raises(SelectionError, match=message):
        run(patch, project.path / ".coverage", None, True, granularity=Granularity.test, base_ref=revision)


def test_outer_function_change_excludes_nested_runtime_lines(scope_project):
    project, _ = scope_project
    path = project.path / "source.py"
    path.write_text(
        "def price():\n    def inner():\n        return 1\n    return inner()\n\ndef other():\n    return 10\n"
    )
    revision = baseline(project)
    path.write_text(path.read_text().replace("return inner()", "return inner() + 1"))
    run_scope(project, revision).assert_outcomes(failed=1, passed=1, deselected=2)


@pytest.mark.parametrize("import_time", [False, True], ids=["uncovered", "import-time"])
def test_scope_without_exclusive_runtime_coverage_requires_original_selection(
    scope_project, coverage_file, import_time
):
    project, revision = scope_project
    change_price(project)
    patch = prepare_diff(project, revision)
    contexts = {"test_helpers.py::test_other|run": {7}}
    if import_time:
        contexts[""] = {2, 3, 4}
    database = coverage_file({"source.py": contexts})
    with pytest.raises(SelectionError, match="Unknown or import-time coverage"):
        run(patch, database, None, True, granularity=Granularity.test, base_ref=revision)


def test_tests_outside_checkout_keep_the_original_selection(scope_project):
    project, revision = scope_project
    change_price(project)
    external = project.path.parent / "test_external_checkout.py"
    external.write_text("def test_external_checkout():\n    assert False\n")
    result = run_scope(project, revision, str(external))
    result.assert_outcomes(failed=1)
    assert "tests outside the checkout; keeping the original selection" in result.stdout.str() + result.stderr.str()
