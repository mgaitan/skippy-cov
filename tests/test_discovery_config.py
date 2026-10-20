from pathlib import Path

import pytest

from skippy_cov import discover_tests_in_file
from skippy_cov.utils import FileTestCandidate, is_test_file


@pytest.mark.parametrize(
    "config",
    [
        ("pyproject.toml", '[tool.pytest.ini_options]\npython_files = ["*.py"]\n'),
        ("pytest.ini", "[pytest]\npython_files = check_*.py\n    legacy.py\n"),
    ],
)
def test_test_file_patterns_support_toml_lists_and_ini_whitespace(tmp_path, monkeypatch, config):
    monkeypatch.chdir(tmp_path)
    Path(config[0]).write_text(config[1])
    Path("legacy.py").write_text(
        "from unittest import TestCase\nclass PriceCase(TestCase):\n    def test_price(self): pass\n"
    )
    assert discover_tests_in_file(Path("legacy.py")) == FileTestCandidate(Path("legacy.py"), set())


def test_default_patterns_include_suffix_style(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert is_test_file(Path("price_test.py"))


def test_config_is_not_reused_after_changing_directory(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "pytest.ini").write_text("[pytest]\npython_files = first_*.py\n")
    (second / "pytest.ini").write_text("[pytest]\npython_files = second_*.py\n")
    monkeypatch.chdir(first)
    assert is_test_file(Path("first_price.py"))
    monkeypatch.chdir(second)
    assert not is_test_file(Path("first_price.py"))
    assert is_test_file(Path("second_price.py"))
