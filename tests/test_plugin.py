import difflib

import pytest


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


def run_skippy(project, *targets, coverage_path=".coverage"):
    return project.runpytest_subprocess(
        "--skippy-cov",
        "--skippy-cov-diff=changes.diff",
        f"--skippy-cov-coverage-file={coverage_path}",
        *(targets or ("legacy.py", "test_other.py")),
    )


def test_real_coverage_selects_legacy_module_and_propagates_failure(baseline_project, change_file):
    change_file()
    result = run_skippy(baseline_project)
    result.assert_outcomes(failed=1, passed=1)
    assert result.ret == 1


def test_exact_node_id_scope_is_not_broadened(baseline_project, change_file):
    change_file()
    result = run_skippy(baseline_project, "legacy.py::PriceCase::test_unrelated")
    result.assert_outcomes(passed=1)


def test_absolute_test_file_scope_is_supported(baseline_project, change_file):
    change_file()
    result = run_skippy(baseline_project, str(baseline_project.path / "legacy.py"))
    result.assert_outcomes(failed=1, passed=1)


@pytest.mark.parametrize(("target", "passed"), [("legacy.py", 1), ("legacy.py::PriceCase::test_price", 0)])
def test_custom_rootdir_does_not_change_selected_path_base(baseline_project, change_file, target, passed):
    (baseline_project.path / "tests").mkdir()
    change_file()
    result = run_skippy(baseline_project, "--rootdir=tests", target)
    result.assert_outcomes(failed=1, passed=passed)


def test_invalid_diff_ref_preserves_original_selection(baseline_project):
    result = baseline_project.runpytest_subprocess(
        "--skippy-cov", "--skippy-cov-diff=missing-base-ref", "legacy.py", "test_other.py"
    )
    result.assert_outcomes(passed=3)
    assert result.ret == 0
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


def test_missing_coverage_preserves_original_selection(baseline_project, change_file):
    change_file()
    result = run_skippy(baseline_project, coverage_path="missing.coverage")
    result.assert_outcomes(failed=1, passed=2)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


def test_legacy_test_body_change_runs_whole_module(baseline_project, change_file):
    source = (baseline_project.path / "legacy.py").read_text().replace("price() == 1", "price() == 99")
    change_file("legacy.py", source)
    run_skippy(baseline_project).assert_outcomes(failed=1, passed=1)


def test_changed_shared_setup_preserves_original_selection(baseline_project, change_file):
    source = (baseline_project.path / "legacy.py").read_text()
    change_file("legacy.py", source + "\n    def setUp(self):\n        self.currency = 'ARS'\n")
    result = run_skippy(baseline_project)
    result.assert_outcomes(passed=3)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


@pytest.mark.parametrize("covered_module", ["test_fixture", "source"])
def test_fixture_named_like_test_preserves_all_consumers(baseline_project, change_file, covered_module):
    baseline_project.makepyfile(
        test_fixture="import pytest\nfrom source import price\n@pytest.fixture(scope='session')\n"
        "def test_client():\n    return 1\n"
        "def test_local(test_client):\n    assert test_client >= price()\n",
        test_consumer="from test_fixture import test_client\n"
        "def test_consumer(test_client):\n    assert test_client == 1\n",
    )
    baseline_project.runpytest_subprocess(
        "test_fixture.py",
        "test_consumer.py",
        "test_other.py",
        f"--cov={covered_module}",
        "--cov-context=test",
        "--cov-report=",
    ).assert_outcomes(passed=3)
    source = (baseline_project.path / "test_fixture.py").read_text().replace("return 1", "return 2")
    change_file("test_fixture.py", source)
    result = run_skippy(baseline_project, "test_fixture.py", "test_consumer.py", "test_other.py")
    result.assert_outcomes(failed=1, passed=2)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


def test_inherited_test_changes_include_subclass_module(baseline_project, change_file):
    baseline_project.makepyfile(
        test_base="from unittest import TestCase\n"
        "class BaseCase(TestCase):\n    amount = 1\n"
        "    def test_amount(self):\n        assert self.amount >= 1\n",
        test_child="import test_base\nclass ChildCase(test_base.BaseCase):\n    amount = 2\n",
    )
    baseline_project.runpytest_subprocess(
        "test_base.py",
        "test_child.py",
        "test_other.py",
        "--cov=test_base",
        "--cov-context=test",
        "--cov-report=",
    ).assert_outcomes(passed=3)
    source = (baseline_project.path / "test_base.py").read_text().replace("self.amount >= 1", "self.amount == 1")
    change_file("test_base.py", source)
    result = run_skippy(baseline_project, "test_base.py", "test_child.py", "test_other.py")
    result.assert_outcomes(failed=1, passed=1)


@pytest.mark.parametrize(
    ("filename", "original", "updated"),
    [("README.md", "# Original\n", "# Updated\n"), ("fixture.json", '{"price": 1}\n', '{"price": 2}\n')],
)
def test_non_python_change_preserves_original_selection(baseline_project, change_file, filename, original, updated):
    (baseline_project.path / filename).write_text(original)
    change_file(filename, updated)
    result = run_skippy(baseline_project)
    result.assert_outcomes(passed=3)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


def test_new_parameterizations_are_not_lost(baseline_project, change_file):
    source = (baseline_project.path / "test_other.py").read_text()
    change_file(
        "test_other.py",
        source + "\nimport pytest\n@pytest.mark.parametrize('amount', [1, 2])\n"
        "def test_new(amount):\n    assert amount < 2\n",
    )
    result = run_skippy(baseline_project)
    result.assert_outcomes(passed=4, failed=1)
    assert "running the original test selection" in result.stdout.str() + result.stderr.str()


def test_empty_diff_does_not_report_success(baseline_project):
    (baseline_project.path / "changes.diff").write_text("")
    result = run_skippy(baseline_project)
    assert result.ret == 5


def test_scope_with_no_affected_module_does_not_report_success(baseline_project, change_file):
    change_file()
    result = run_skippy(baseline_project, "test_other.py")
    assert result.ret == 5
