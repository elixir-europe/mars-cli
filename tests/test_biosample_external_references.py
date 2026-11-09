import pytest
from mars_lib.biosamples_external_references import (
    BiosamplesRecord,
    load_json_file,
    handle_input_dict,
    get_header,
    validate_bs_accession,
)


def test_load_json_file():

    with pytest.raises(
        FileNotFoundError,
        match="The file './tests/fixtures/where_is_my_json.json' does not exist.",
    ):
        load_json_file("./tests/fixtures/where_is_my_json.json")

    with pytest.raises(
        ValueError,
        match="The path './tests/fixtures' is not a file.",
    ):
        load_json_file("./tests/fixtures")

    with pytest.raises(
        ValueError,
        match="The given file './tests/fixtures/not_a_json_file.txt' is not a JSON file based on its extension.",
    ):
        load_json_file("./tests/fixtures/not_a_json_file.txt")

    with pytest.raises(
        ValueError,
        match="The file content of the given file './tests/fixtures/bad_json.json' is not valid JSON.",
    ):
        load_json_file("./tests/fixtures/bad_json.json")


def test_handle_input_dict():
    input = "MARS/test-data/ISA-BH2023-ALL/isa-bh2023-all.json"
    assert handle_input_dict(input)

    not_a_json = "./tests/fixtures/bad_json.json"
    with pytest.raises(
        ValueError,
        match=f"The file content of the given file '{not_a_json}' is not valid JSON.",
    ):
        handle_input_dict(not_a_json)


def test_get_header():
    token = "jkdcbjkbkjsdbjkvb8347yrfiegh3ru3y29yf2g8i39u"
    header = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/hal+json",
        "Authorization": "Bearer jkdcbjkbkjsdbjkvb8347yrfiegh3ru3y29yf2g8i39u",
    }
    assert get_header(token) == header


def test_validate_bs_accession():
    invalid_accession = "SAREA112654119"

    with pytest.raises(
        ValueError,
        match=f"The provided accession string '{invalid_accession}' does not match the required format.",
    ):
        validate_bs_accession(invalid_accession)

    valid_accession = "SAMEA112654119"
    validate_bs_accession(valid_accession)


def test_validate_json_against_schema():
    pass


def test_fetch_bs_json():
    bs_record = BiosamplesRecord("SAMEA112654119")

    # Bad end-point
    with pytest.raises(RuntimeError):
        bs_record.fetch_bs_json("https://wwwdev.ebi.ac.uk/biosamples/sample/")

    # Correct end-point
    response = bs_record.fetch_bs_json("https://wwwdev.ebi.ac.uk/biosamples/samples/")
    assert response["accession"] == "SAMEA112654119"


def test_load_bs_json():
    bs_record_from_json = BiosamplesRecord("SAMEA112654119")
    bs_record_from_json.load_bs_json("./tests/fixtures/SAMEA112654119.json")
    assert bs_record_from_json.bs_json["accession"] == "SAMEA112654119"

    bs_dict = load_json_file("./tests/fixtures/SAMEA112654119.json")
    bs_record_from_dict = BiosamplesRecord("SAMEA112654119")
    bs_record_from_dict.load_bs_json(bs_dict)
    assert bs_record_from_dict.bs_json["accession"] == "SAMEA112654119"

    bs_record_from_bad_json = BiosamplesRecord("SAMEA112654119")
    with pytest.raises(
        ValueError,
        match="The file content of the given file './tests/fixtures/bad_json.json' is not valid JSON.",
    ):
        bs_record_from_bad_json.load_bs_json("./tests/fixtures/bad_json.json")

    bs_record_from_bad_dict = BiosamplesRecord("SAMEA112654119")
    with pytest.raises(
        FileNotFoundError,
        match="The file 'This is not even a dict!' does not exist.",
    ):
        bs_record_from_bad_dict.load_bs_json("This is not even a dict!")


def test_extend_externalReferences():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.load_bs_json("./tests/fixtures/SAMEA112654119.json")

    new_ext_refs_list = []
    bs_record.extend_externalReferences(new_ext_refs_list)

    existing_refs = [
        {"url": "https://ega-archive.org/datasets/EGAD00010002458", "duo": []},
        {
            "url": "https://ega-archive.org/metadata/v2/samples/EGAN00004248937",
            "duo": [],
        },
        {"url": "https://www.ebi.ac.uk/ena/browser/view/SAMEA112654119", "duo": []},
    ]

    new_refs = [
        {"url": "https://ega-archive.org/datasets/1", "duo": []},
        {
            "url": "https://ega-archive.org/metadata/v2/samples/2",
            "duo": [],
        },
        {"url": "https://www.ebi.ac.uk/ena/browser/view/3", "duo": []},
    ]

    assert len(existing_refs) == len(bs_record.bs_json["externalReferences"])

    # Order of the external links is different in the BiosamplesRecord from a json,
    # downloaded from https://wwwdev.ebi.ac.uk/biosamples/samples/
    for ext_link in existing_refs:
        assert ext_link in bs_record.bs_json["externalReferences"]

    for ext_link in new_refs:
        assert ext_link not in bs_record.bs_json["externalReferences"]

    bs_record.extend_externalReferences(new_refs)

    for ext_link in existing_refs + new_refs:
        assert ext_link in bs_record.bs_json["externalReferences"]


def test_update_remote_record():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.fetch_bs_json("https://wwwdev.ebi.ac.uk/biosamples/samples/")

    with pytest.raises(RuntimeError):
        bs_record.update_remote_record(get_header("thisIsAFakeToken"))


# -- #
# Helpers and tests with mocked network access
# -- #
import json
from unittest.mock import MagicMock, patch

import requests
from jsonschema.exceptions import SchemaError, ValidationError

from mars_lib.biosamples_external_references import (
    _biosamples_used_in_assay,
    _extract_accessions,
    _fetch_assay_accession,
    _fetch_assay_target_repo,
    _get_sample_accession,
    biosamples_endpoints,
    input_json_schema_filepath,
    update_external_references,
    validate_json_against_schema,
)
from mars_lib.models.isa_json import Assay, Comment, Investigation, Sample

MODULE = "mars_lib.biosamples_external_references"


def _sample(sample_id: str, accession: str | None = None) -> dict:
    characteristics = []
    if accession:
        characteristics.append(
            {
                "category": {"characteristicType": {"annotationValue": "accession"}},
                "value": accession,
            }
        )
    return {"@id": sample_id, "name": sample_id, "characteristics": characteristics}


def _assay(assay_id: str, repo: str, samples: list[str], accession=None) -> dict:
    comments = [{"name": "target_repository", "value": repo}]
    if accession:
        comments.append({"name": "assay_accession", "value": accession})
    return {
        "@id": assay_id,
        "filename": f"a_{assay_id}.txt",
        "comments": comments,
        "materials": {"samples": [{"@id": s} for s in samples]},
    }


@pytest.fixture
def investigation() -> Investigation:
    return Investigation.model_validate(
        {
            "title": "inv",
            "studies": [
                {
                    "@id": "study1",
                    "title": "study one",
                    "materials": {
                        "samples": [
                            _sample("s1", "SAMEA1"),
                            _sample("s2", "SAMEA2"),
                            _sample("s3"),  # no accession
                        ]
                    },
                    "assays": [
                        _assay("a1", "ena", ["s1", "s2", "s3"], "ERX1"),
                        _assay("a2", "metabolights", ["s1"]),  # no assay accession
                    ],
                },
                {"@id": "study2", "title": "empty study"},
            ],
        }
    )


URLS = {"ENA": {"EXTERNAL-REF-URL": "https://www.ebi.ac.uk/ena/browser/view/"}}


def test_extract_accessions(investigation):
    studies = _extract_accessions(investigation)

    assert [s["id"] for s in studies] == ["study1", "study2"]
    assert studies[1]["assays"] == []

    a1, a2 = studies[0]["assays"]
    assert (a1["id"], a1["accession"], a1["target_repo"]) == ("a1", "ERX1", "ena")
    assert [s.id for s in a1["biosamples"]] == ["s1", "s2", "s3"]
    assert a2["accession"] is None
    assert [s.id for s in a2["biosamples"]] == ["s1"]


def test_biosamples_used_in_assay(investigation):
    study = investigation.studies[0]
    samples = study.materials.samples

    assert [s.id for s in _biosamples_used_in_assay(samples, study.assays[1])] == ["s1"]

    # Assay without materials uses no samples
    no_materials = Assay.model_construct(materials=None)
    assert _biosamples_used_in_assay(samples, no_materials) == []


def test_fetch_assay_accession_and_target_repo(investigation):
    ena_assay, mtbls_assay = investigation.studies[0].assays
    assert _fetch_assay_accession(ena_assay) == "ERX1"
    assert _fetch_assay_accession(mtbls_assay) is None
    assert _fetch_assay_target_repo(ena_assay) == "ena"


def test_get_sample_accession():
    with_accession = Sample.model_validate(_sample("s1", "SAMEA1"))
    without = Sample.model_validate(_sample("s2"))
    no_category = Sample.model_validate(
        {"@id": "s3", "characteristics": [{"value": "x"}]}
    )

    assert _get_sample_accession(with_accession) == "SAMEA1"
    assert _get_sample_accession(without) is None
    assert _get_sample_accession(no_category) is None


def test_update_external_references(investigation):
    result = update_external_references(investigation, URLS)

    # Only the assay with an accession (a1) contributes; s3 has no accession
    assert result == {
        "biosampleExternalReferences": [
            {
                "biosampleAccession": "SAMEA1",
                "externalReferences": [
                    {"url": "https://www.ebi.ac.uk/ena/browser/view/ERX1", "duo": []}
                ],
            },
            {
                "biosampleAccession": "SAMEA2",
                "externalReferences": [
                    {"url": "https://www.ebi.ac.uk/ena/browser/view/ERX1", "duo": []}
                ],
            },
        ]
    }

    # The payload must satisfy the input schema used by create_external_references
    assert validate_json_against_schema(result, input_json_schema_filepath)


def test_update_external_references_no_duplicates(investigation):
    # Same assay twice -> same reference must not be repeated for a sample
    study = investigation.studies[0]
    study.assays.append(study.assays[0])

    result = update_external_references(investigation, URLS)
    for entry in result["biosampleExternalReferences"]:
        assert len(entry["externalReferences"]) == 1


def test_update_external_references_missing_url(investigation):
    with pytest.raises(ValueError, match="No 'EXTERNAL-REF-URL' found"):
        update_external_references(investigation, {"ENA": {}})


def test_handle_input_dict_with_dict_and_non_dict_file(tmp_path):
    assert handle_input_dict({"a": "b"}) == {"a": "b"}

    list_file = tmp_path / "list.json"
    list_file.write_text("[1, 2]")
    with pytest.raises(ValueError, match="does not contain a valid dictionary"):
        handle_input_dict(str(list_file))


def test_validate_json_against_schema_errors(tmp_path):
    with pytest.raises(ValidationError):
        validate_json_against_schema({"wrong": 1}, input_json_schema_filepath)

    with pytest.raises(SchemaError):
        validate_json_against_schema({}, {"type": "not-a-type"})

    # Both arguments can be file paths
    doc = tmp_path / "doc.json"
    doc.write_text(
        json.dumps(
            {
                "biosampleExternalReferences": [
                    {
                        "biosampleAccession": "SAMEA112654119",
                        "externalReferences": [{"url": "https://x.org", "duo": []}],
                    }
                ]
            }
        )
    )
    assert validate_json_against_schema(str(doc), input_json_schema_filepath)


def test_biosamples_record_init_and_display(capsys):
    with pytest.raises(ValueError):
        BiosamplesRecord("not-an-accession")

    record = BiosamplesRecord("SAMEA112654119")
    record.display()
    out = capsys.readouterr().out
    assert "Production Mode: False" in out


def test_load_bs_json_invalid_type():
    with pytest.raises(ValueError, match="Neither the file"):
        BiosamplesRecord("SAMEA112654119").load_bs_json(42)  # type: ignore[arg-type]


def _response(status=200, payload=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = payload
    return resp


def test_fetch_bs_json_mocked():
    record = BiosamplesRecord("SAMEA112654119")
    endpoint = biosamples_endpoints["dev"]

    with patch(f"{MODULE}.requests.get") as get:
        get.return_value = _response(200, {"accession": "SAMEA112654119"})
        assert record.fetch_bs_json(endpoint) == {"accession": "SAMEA112654119"}
        get.assert_called_once_with(f"{endpoint}SAMEA112654119.json")

        get.return_value = _response(404, text="nope")
        with pytest.raises(RuntimeError, match="Expected status code 200"):
            record.fetch_bs_json(endpoint)

        get.return_value = _response(200, ["not", "a", "dict"], text="[]")
        with pytest.raises(ValueError, match="not a valid dictionary"):
            record.fetch_bs_json(endpoint)

        bad_json = _response(200, text="<html>")
        bad_json.json.side_effect = json.JSONDecodeError("bad", "<html>", 0)
        get.return_value = bad_json
        with pytest.raises(ValueError, match="not valid JSON"):
            record.fetch_bs_json(endpoint)

        get.side_effect = requests.ConnectionError("down")
        with pytest.raises(RuntimeError, match="Error making the request"):
            record.fetch_bs_json(endpoint)


def test_pop_links():
    record = BiosamplesRecord("SAMEA112654119")
    record.load_bs_json({"accession": "SAMEA112654119", "_links": {"self": {}}})
    assert "_links" not in record.pop_links()
    # Idempotent when "_links" is already gone
    assert "_links" not in record.pop_links()


def test_extend_external_references_without_existing_refs():
    record = BiosamplesRecord("SAMEA112654119")
    record.load_bs_json({"accession": "SAMEA112654119"})
    new_refs = [{"url": "https://x.org/1", "duo": []}]

    assert record.extend_externalReferences(new_refs)["externalReferences"] == new_refs


def test_extend_external_references_fetches_when_empty():
    record = BiosamplesRecord("SAMEA112654119")
    record.bs_json = {}  # nothing loaded yet

    def fake_fetch(endpoint):
        # Dev endpoint is used when production is False
        assert endpoint == biosamples_endpoints["dev"]
        record.bs_json = {"accession": "SAMEA112654119"}

    with patch.object(record, "fetch_bs_json", side_effect=fake_fetch) as fetch:
        record.extend_externalReferences([{"url": "https://x.org", "duo": []}])
        fetch.assert_called_once()

    record = BiosamplesRecord("SAMEA112654119")
    record.production = True
    record.bs_json = {}
    with patch.object(record, "fetch_bs_json") as fetch:
        record.bs_json = {}
        record.extend_externalReferences([])
        fetch.assert_called_once_with(biosamples_endpoints["prod"])


def test_update_remote_record_mocked():
    record = BiosamplesRecord("SAMEA112654119")
    record.biosamples_url = "https://example.org/SAMEA112654119.json"
    record.bs_json = {"accession": "SAMEA112654119"}
    header = get_header("token")

    with patch(f"{MODULE}.requests.put") as put:
        put.return_value = _response(200, text="updated")
        assert record.update_remote_record(header) == "updated"
        put.assert_called_once_with(
            "https://example.org/SAMEA112654119.json?authProvider=WEBIN",
            headers=header,
            data=json.dumps({"accession": "SAMEA112654119"}),
        )

        put.return_value = _response(401, text="unauthorized")
        with pytest.raises(RuntimeError, match="Expected status code 200"):
            record.update_remote_record(header)

        put.side_effect = requests.Timeout("slow")
        with pytest.raises(RuntimeError, match="Error making the request"):
            record.update_remote_record(header)


def test_fetch_assay_target_repo_without_value():
    assay = Assay.model_construct(
        id="a1", comments=[Comment(name="target_repository", value=None)]
    )
    with pytest.raises(ValueError, match="has no value"):
        _fetch_assay_target_repo(assay)
