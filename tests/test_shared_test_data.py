"""Run the pydantic models over every shared test-data file.

Files added to MARS/test-data are picked up automatically, so a file the models
cannot load, or would silently change, fails here instead of during a submission.
"""

import json
from pathlib import Path

import pytest

from mars_lib.models.isa_json import Investigation
from mars_lib.models.repository_response import RepositoryResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATA = REPO_ROOT / "MARS" / "test-data"

# Files the models cannot load yet. strict=True: when one starts passing, the
# test fails so the entry gets removed.
KNOWN_ISA_FAILURES = {
    "MARS/test-data/ISA-PTX-TEST/isajson/i_investigation_PTX_Pilot_Daphnia_magna.json": (
        "PTX uses fields the models do not allow, e.g. ontologySourceReferences[0].@id"
    ),
}


def _isa_json_files():
    shared = [path for path in TEST_DATA.rglob("*.json") if "receipt" not in path.name]
    fixtures = [
        *(REPO_ROOT / "tests" / "fixtures" / "isa_jsons").glob("*.json"),
        REPO_ROOT / "tests" / "fixtures" / "minimal_valid_investigation.json",
    ]
    params = []
    for path in sorted(shared) + sorted(fixtures):
        name = path.relative_to(REPO_ROOT).as_posix()
        reason = KNOWN_ISA_FAILURES.get(name)
        marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
        params.append(pytest.param(path, id=name, marks=marks))
    return params


def _short(value):
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _differences(expected, actual, path="$"):
    """Paths where `actual` differs from `expected`, for a readable failure."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        differences = []
        for key in expected.keys() | actual.keys():
            if key not in actual:
                differences.append(f"{path}.{key}: dropped")
            elif key not in expected:
                differences.append(f"{path}.{key}: added")
            else:
                differences += _differences(expected[key], actual[key], f"{path}.{key}")
        return differences
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: {len(expected)} items -> {len(actual)}"]
        return [
            difference
            for index, (e, a) in enumerate(zip(expected, actual))
            for difference in _differences(e, a, f"{path}[{index}]")
        ]
    if expected == actual:
        return []
    return [f"{path}: {_short(expected)} -> {_short(actual)}"]


@pytest.mark.parametrize("path", _isa_json_files())
def test_isa_json_loads_and_round_trips(path):
    data = json.loads(path.read_text())

    investigation = Investigation.model_validate(data)

    dumped = investigation.model_dump(mode="json", by_alias=True, exclude_unset=True)
    differences = sorted(_differences(data, dumped))
    assert not differences, "The models change this file:\n" + "\n".join(
        differences[:20]
    )


# The MARS receipts the repository services write; ena-receipt*.json are raw
# ENA receipts, not MARS receipts.
@pytest.mark.parametrize(
    "path", sorted(TEST_DATA.glob("mars-*receipt*.json")), ids=lambda path: path.name
)
def test_mars_receipt_parses(path):
    RepositoryResponse.from_json_file(str(path))
