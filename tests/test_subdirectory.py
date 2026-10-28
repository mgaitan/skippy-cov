import json
import subprocess
import sys
from pathlib import Path

import pytest

from skippy_cov.__main__ import Format, get_diff_content, run
from skippy_cov.scopes import Granularity


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def checkout(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    root = pytester.path
    monkeypatch.setenv("COVERAGE_FILE", str(root / ".coverage"))
    pytester.makeini("[pytest]\npythonpath = .\nmarkers = affected\ntestpaths = suite\n")
    (root / ".coveragerc").write_text("[run]\nrelative_files = true\ncore = ctrace\n")
    pytester.makepyfile(source="def price():\n    return 1\n\ndef other():\n    return 10\n")
    suite = root / "suite"
    suite.mkdir()
    (suite / "test_price.py").write_text(
        "import pytest\nfrom source import price, other\n"
        "@pytest.mark.affected\ndef test_price():\n    assert price() == 1\n"
        "def test_other():\n    assert other() == 10\n"
    )
    (root / "test_outside.py").write_text("from source import price\ndef test_outside():\n    assert price() == 1\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "Tests")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    revision = git(root, "rev-parse", "HEAD")
    pytester.runpytest_subprocess(".", "--cov=source", "--cov-context=test", "--cov-report=").assert_outcomes(passed=3)
    (root / "source.py").write_text((root / "source.py").read_text().replace("return 1\n", "return 2\n"))
    patch = subprocess.check_output(["git", "diff", "--"], cwd=root, text=True)
    (suite / "changes.diff").write_text(patch)
    git(root, "add", "source.py")
    git(root, "commit", "-qm", "change")
    monkeypatch.chdir(suite)
    return pytester, revision


def plugin_run(checkout, *args):
    project, revision = checkout
    return project.runpytest_subprocess(
        "--skippy-cov",
        "--skippy-cov-diff=changes.diff",
        "--skippy-cov-coverage-file=../.coverage",
        f"--skippy-cov-base-ref={revision}",
        *args,
    )


def test_cli_module_targets_are_invocation_relative(checkout):
    assert run(get_diff_content("changes.diff"), Path("../.coverage"), [Path(".")], True) == {"test_price.py"}


def test_cli_strip_prefix_uses_invocation_relative_scope(checkout):
    assert run(get_diff_content("changes.diff"), Path("../.coverage"), [Path("../suite")], False) == {"test_price.py"}


def test_cli_git_diff_ignores_relative_diff_configuration(checkout):
    project, revision = checkout
    git(project.path, "config", "diff.relative", "true")
    assert "b/source.py" in get_diff_content(revision)
    assert run(get_diff_content(revision), Path("../.coverage"), None, True) == {"test_price.py", "../test_outside.py"}


def test_cli_individual_collection_preserves_pytest_discovery(checkout):
    _, revision = checkout
    assert run(
        get_diff_content("changes.diff"),
        Path("../.coverage"),
        None,
        True,
        Format.json,
        granularity=Granularity.test,
        base_ref=revision,
        collect=True,
    ) == {"test_price.py::test_price"}


def test_cli_collection_keeps_exact_nodeid_and_keyword(checkout):
    _, revision = checkout
    assert (
        run(
            get_diff_content("changes.diff"),
            Path("../.coverage"),
            None,
            True,
            granularity=Granularity.test,
            base_ref=revision,
            collect=True,
            pytest_args=["test_price.py::test_other", "-k", "other"],
        )
        == set()
    )


def test_plugin_module_selection_does_not_collect_outside_default_scope(checkout):
    plugin_run(checkout).assert_outcomes(failed=1, passed=1)


def test_plugin_module_keeps_exact_nodeid(checkout):
    plugin_run(checkout, "test_price.py::test_other").assert_outcomes(passed=1)


def test_plugin_module_keeps_keyword_expression(checkout):
    plugin_run(checkout, "-k", "other").assert_outcomes(passed=1, deselected=1)


def test_plugin_individual_keeps_mark_expression(checkout):
    plugin_run(checkout, "--skippy-cov-granularity=test", "-m", "not affected").assert_outcomes(deselected=2)


def test_plugin_individual_inspects_source_outside_invocation_directory(checkout):
    plugin_run(checkout, "--skippy-cov-granularity=test").assert_outcomes(failed=1, deselected=1)


def test_absolute_real_coverage_from_checkout_subdirectory(checkout):
    project, _ = checkout
    # Re-record the baseline with absolute source paths, without changing test contexts.
    source = project.path / "source.py"
    source.write_text(source.read_text().replace("return 2\n", "return 1\n"))
    (project.path / ".coveragerc").write_text("[run]\ncore = ctrace\n")
    project.runpytest_subprocess(
        "--cov=source", "--cov-config=../.coveragerc", "--cov-context=test", "--cov-report="
    ).assert_outcomes(passed=2)
    source.write_text(source.read_text().replace("return 1\n", "return 2\n"))
    assert run(get_diff_content("changes.diff"), Path("../.coverage"), None, True) == {"test_price.py"}


def test_relocated_real_coverage_maps_original_checkout(checkout, tmp_path):
    project, _ = checkout
    source = project.path / "source.py"
    source.write_text(source.read_text().replace("return 2\n", "return 1\n"))
    (project.path / ".coveragerc").write_text("[run]\ncore = ctrace\n")
    project.runpytest_subprocess(
        "--cov=source", "--cov-config=../.coveragerc", "--cov-context=test", "--cov-report="
    ).assert_outcomes(passed=2)
    relocated = tmp_path / "relocated"
    project.path.rename(relocated)
    source = relocated / "source.py"
    source.write_text(source.read_text().replace("return 1\n", "return 2\n"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "skippy_cov",
            "--diff=changes.diff",
            "--coverage-file=../.coverage",
            f"--source-root={project.path}",
            "--format=json",
        ],
        cwd=relocated / "suite",
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"test_price.py": []}


def test_plugin_unmappable_coverage_keeps_original_subdirectory_selection(checkout):
    project, _ = checkout
    source = project.path / "source.py"
    source.write_text(source.read_text().replace("return 2\n", "return 1\n"))
    (project.path / ".coveragerc").write_text("[run]\ncore = ctrace\n")
    project.runpytest_subprocess(
        "--cov=source", "--cov-config=../.coveragerc", "--cov-context=test", "--cov-report="
    ).assert_outcomes(passed=2)
    source.write_text(source.read_text().replace("return 1\n", "return 2\n"))
    result = plugin_run(checkout, "--skippy-cov-source-root=/unrelated-checkout")
    result.assert_outcomes(failed=1, passed=1)
    assert "running the original test selection" in result.stderr.str()


def test_plugin_individual_keeps_exact_unaffected_nodeid(checkout):
    plugin_run(checkout, "--skippy-cov-granularity=test", "test_price.py::test_other").assert_outcomes(deselected=1)


@pytest.fixture
def relative_subdir_checkout(pytester, monkeypatch):
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    root = pytester.path
    package = root / "packages" / "api"
    package.mkdir(parents=True)
    monkeypatch.setenv("COVERAGE_FILE", str(root / ".coverage"))
    (package / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (package / ".coveragerc").write_text("[run]\nrelative_files = true\ncore = ctrace\n")
    (package / "source.py").write_text("def price():\n    return 1\n")
    (package / "test_source.py").write_text(
        "from source import price\ndef test_price():\n    assert price() == 1\ndef test_other():\n    assert True\n"
    )
    # Colliding root names must never become the selected consumers of the package.
    (root / "source.py").write_text("def price():\n    return 100\n")
    (root / "test_source.py").write_text("def test_root():\n    assert True\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "tests@example.com")
    git(root, "config", "user.name", "Tests")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    revision = git(root, "rev-parse", "HEAD")
    monkeypatch.chdir(package)
    pytester.runpytest_subprocess("--cov=source", "--cov-context=test", "--cov-report=").assert_outcomes(passed=2)
    (package / "source.py").write_text("def price():\n    return 2\n")
    git(root, "add", "packages/api/source.py")
    git(root, "commit", "-qm", "change")
    return pytester, package, revision


def test_relative_subdir_cli_module_root_and_subdir_equivalence(relative_subdir_checkout, monkeypatch):
    project, _, revision = relative_subdir_checkout
    assert run(get_diff_content(revision), project.path / ".coverage", None, True, source_root=Path(".")) == {
        "test_source.py"
    }
    monkeypatch.chdir(project.path)
    assert run(get_diff_content(revision), Path(".coverage"), None, True, source_root=Path("packages/api")) == {
        "packages/api/test_source.py"
    }


def test_relative_subdir_cli_individual_root_and_subdir_equivalence(relative_subdir_checkout, monkeypatch):
    project, _, revision = relative_subdir_checkout
    assert run(
        get_diff_content(revision),
        project.path / ".coverage",
        None,
        True,
        source_root=Path("."),
        granularity=Granularity.test,
        base_ref=revision,
        collect=True,
    ) == {"test_source.py::test_price"}
    monkeypatch.chdir(project.path)
    assert run(
        get_diff_content(revision),
        Path(".coverage"),
        [Path("packages/api")],
        True,
        source_root=Path("packages/api"),
        granularity=Granularity.test,
        base_ref=revision,
        collect=True,
    ) == {"packages/api/test_source.py::test_price"}


def test_relative_subdir_cli_missing_origin_errors_instead_of_matching_collision(relative_subdir_checkout):
    project, _, revision = relative_subdir_checkout
    result = subprocess.run(
        [sys.executable, "-m", "skippy_cov", f"--diff={revision}", f"--coverage-file={project.path / '.coverage'}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "specify --source-root" in result.stderr


def test_relative_subdir_plugin_module_root_and_subdir_equivalence(relative_subdir_checkout, monkeypatch):
    project, _, revision = relative_subdir_checkout
    args = ["--skippy-cov", f"--skippy-cov-diff={revision}", f"--skippy-cov-coverage-file={project.path / '.coverage'}"]
    project.runpytest_subprocess(*args, "--skippy-cov-source-root=.").assert_outcomes(failed=1, passed=1)
    monkeypatch.chdir(project.path)
    project.runpytest_subprocess(*args, "--skippy-cov-source-root=packages/api", "packages/api").assert_outcomes(
        failed=1, passed=1
    )


def test_relative_subdir_plugin_individual_root_and_subdir_equivalence(relative_subdir_checkout, monkeypatch):
    project, _, revision = relative_subdir_checkout
    args = [
        "--skippy-cov",
        "--skippy-cov-granularity=test",
        f"--skippy-cov-diff={revision}",
        f"--skippy-cov-coverage-file={project.path / '.coverage'}",
    ]
    project.runpytest_subprocess(*args, "--skippy-cov-source-root=.").assert_outcomes(failed=1, deselected=1)
    monkeypatch.chdir(project.path)
    project.runpytest_subprocess(*args, "--skippy-cov-source-root=packages/api", "packages/api").assert_outcomes(
        failed=1, deselected=1
    )


def test_relative_subdir_plugin_missing_origin_falls_back(relative_subdir_checkout):
    project, _, revision = relative_subdir_checkout
    result = project.runpytest_subprocess(
        "--skippy-cov", f"--skippy-cov-diff={revision}", f"--skippy-cov-coverage-file={project.path / '.coverage'}"
    )
    result.assert_outcomes(failed=1, passed=1)
    assert "specify --source-root" in result.stderr.str()
    assert "running the original test selection" in result.stderr.str()
