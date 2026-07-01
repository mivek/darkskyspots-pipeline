"""Project scaffolding tests (validation fixtures, etc)."""
import json


def test_checkpoints_json_valid():
    with open("validation/checkpoints.json") as f:
        data = json.load(f)
    assert isinstance(data, list)
