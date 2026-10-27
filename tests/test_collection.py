import json
from types import FunctionType, SimpleNamespace

from skippy_cov.collect import Collector


def test_custom_and_generated_items_round_trip_without_source_definitions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def generated():
        pass

    generated = FunctionType(generated.__code__.replace(co_filename="<generated-test>"), {})
    outside = tmp_path.parent / "external.py"
    items = [
        SimpleNamespace(path=tmp_path / "test_custom.py", nodeid="test_custom.py::custom"),
        SimpleNamespace(path=tmp_path / "generated.py", nodeid="generated.py::generated", obj=generated),
        SimpleNamespace(path=outside, nodeid=f"{outside}::external"),
    ]
    output = tmp_path / "items.json"
    Collector(output).pytest_collection_finish(SimpleNamespace(items=items))
    assert json.loads(output.read_text()) == [
        {"path": "test_custom.py", "name": "custom", "definition": None},
        {"path": "generated.py", "name": "generated", "definition": None},
        {"path": outside.as_posix(), "name": "external", "definition": None},
    ]
