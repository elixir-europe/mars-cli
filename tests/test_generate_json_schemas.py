"""Checks on scripts/generate_json_schemas.py, which publishes the MARS schemas."""

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def _load_generator():
    path = REPO_ROOT / "scripts" / "generate_json_schemas.py"
    spec = importlib.util.spec_from_file_location("generate_json_schemas", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


def test_schema_is_valid_and_rooted_at_investigation():
    schema = generator.isa_json_schema()

    Draft202012Validator.check_schema(schema)
    assert schema["$ref"] == "#/$defs/Investigation"
    # The MARS docs site renders one table per entry in $defs.
    assert {"Investigation", "Study", "Assay"} <= schema["$defs"].keys()


def test_validators_are_listed_per_model():
    validators = generator.custom_validators()

    assert "Investigation.validate_filename" in validators
    assert validators["Assay.validate_filename"]["fields"] == ["filename"]
    assert all(v["source"].lstrip().startswith("@") for v in validators.values())


@pytest.mark.parametrize(
    "fixture, valid",
    [
        ("minimal_valid_investigation.json", True),
        ("isa_jsons/1_after_biosamples.json", True),
        ("invalid_investigation.json", False),
    ],
)
def test_schema_agrees_with_models(fixture, valid):
    data = json.loads((FIXTURES / fixture).read_text())

    errors = list(Draft202012Validator(generator.isa_json_schema()).iter_errors(data))

    assert (not errors) == valid, [error.message for error in errors]


def test_writes_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["generate", "--output-dir", str(tmp_path)])

    generator.main()

    written = {path.name: json.loads(path.read_text()) for path in tmp_path.iterdir()}
    assert written.keys() == {
        "isa_json.schema.json",
        "isa_json_schema_with_validators.json",
    }
    assert (
        written["isa_json_schema_with_validators.json"]["schema"]
        == written["isa_json.schema.json"]
    )
