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
