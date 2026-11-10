import copy
import json
from unittest import mock

import pytest
from jsonschema.exceptions import ValidationError

from mars_lib.biosamples_external_references import (
    BiosamplesRecord,
    biosamples_endpoints,
    input_json_schema_filepath,
    load_json_file,
    handle_input_dict,
    get_header,
    validate_bs_accession,
    validate_json_against_schema,
    webin_token_endpoints,
)
from mars_lib.submit import create_external_references

BS_FIXTURE = "./tests/fixtures/SAMEA112654119.json"
EXT_REFS_INPUT = "./tests/test_input.json"


def _response(status_code=200, payload=None, text=""):
    response = mock.Mock(status_code=status_code, text=text)
    response.json.return_value = payload
    response.content = text.encode()
    return response


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


def test_validate_json_against_schema(tmp_path, monkeypatch):
    ext_refs = load_json_file(EXT_REFS_INPUT)

    # The schema ships with the package, so it is found from any working directory
    monkeypatch.chdir(tmp_path)
    assert validate_json_against_schema(ext_refs, input_json_schema_filepath)

    ext_refs["biosampleExternalReferences"][0]["biosampleAccession"] = "SAREA112654119"
    with pytest.raises(ValidationError):
        validate_json_against_schema(ext_refs, input_json_schema_filepath)


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


def test_extend_externalReferences_skips_refs_already_on_record():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.load_bs_json(BS_FIXTURE)
    existing_refs = copy.deepcopy(bs_record.bs_json["externalReferences"])

    bs_record.extend_externalReferences(
        [
            # BioSamples returns `"duo": []`, the input schema allows leaving it out
            {"url": "https://ega-archive.org/datasets/EGAD00010002458"},
            {"url": "https://example.org/new", "duo": ["DUO:0000007", "DUO:0000005"]},
            # Same as the previous one, DUO codes in another order
            {"url": "https://example.org/new", "duo": ["DUO:0000005", "DUO:0000007"]},
        ]
    )

    # Existing references keep their order, the new one is appended once
    assert bs_record.bs_json["externalReferences"] == existing_refs + [
        {"url": "https://example.org/new", "duo": ["DUO:0000007", "DUO:0000005"]},
    ]
    assert "_links" not in bs_record.bs_json


def test_extend_externalReferences_fetches_record_when_not_loaded():
    bs_record = BiosamplesRecord("SAMEA112654119")

    with mock.patch(
        "mars_lib.biosamples_external_references.requests.get",
        return_value=_response(payload=load_json_file(BS_FIXTURE)),
    ) as get:
        bs_record.extend_externalReferences([{"url": "https://example.org/1"}])

    assert get.call_args.args[0] == f"{biosamples_endpoints['dev']}SAMEA112654119.json"
    assert {"url": "https://example.org/1"} in bs_record.bs_json["externalReferences"]


def test_update_remote_record_after_load_bs_json():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.load_bs_json(BS_FIXTURE)
    bs_record.extend_externalReferences([{"url": "https://example.org/1"}])

    with mock.patch(
        "mars_lib.biosamples_external_references.requests.put",
        return_value=_response(text="{}"),
    ) as put:
        bs_record.update_remote_record(get_header("token"))

    assert (
        put.call_args.args[0]
        == f"{biosamples_endpoints['dev']}SAMEA112654119?authProvider=WEBIN"
    )
    assert json.loads(put.call_args.kwargs["data"]) == bs_record.bs_json


@pytest.mark.parametrize("production", [False, True])
def test_create_external_references(production):
    environment = "prod" if production else "dev"
    endpoint = biosamples_endpoints[environment]
    records = {
        "SAMEA112654119": load_json_file(BS_FIXTURE),
        "SAMEA419425": {"accession": "SAMEA419425", "name": "no refs yet"},
    }

    def fake_get(url, headers=None):
        accession = url.removeprefix(endpoint).removesuffix(".json")
        return _response(payload=copy.deepcopy(records[accession]))

    with (
        mock.patch(
            "mars_lib.authentication.requests.post",
            return_value=_response(text="the-token"),
        ) as post,
        mock.patch(
            "mars_lib.biosamples_external_references.requests.get",
            side_effect=fake_get,
        ) as get,
        mock.patch(
            "mars_lib.biosamples_external_references.requests.put",
            return_value=_response(text="{}"),
        ) as put,
    ):
        create_external_references(
            {"username": "Webin-00000", "password": "secret"},
            load_json_file(EXT_REFS_INPUT),
            production,
        )

    # Token is requested from the Webin instance matching the BioSamples instance
    assert post.call_args.args[0].startswith(webin_token_endpoints[environment])
    # Records are fetched with the token, so private samples can be updated too
    assert all(
        c.kwargs["headers"] == get_header("the-token") for c in get.call_args_list
    )
    assert [c.args[0] for c in put.call_args_list] == [
        f"{endpoint}SAMEA112654119?authProvider=WEBIN",
        f"{endpoint}SAMEA419425?authProvider=WEBIN",
    ]

    first_update, second_update = (
        json.loads(c.kwargs["data"]) for c in put.call_args_list
    )
    # All input refs are already on SAMEA112654119, so nothing is added
    assert (
        first_update["externalReferences"]
        == records["SAMEA112654119"]["externalReferences"]
    )
    assert second_update["externalReferences"] == [
        {"url": "https://ega-archive.org/datasets/EGAD00010002458"}
    ]


def test_update_remote_record():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.fetch_bs_json("https://wwwdev.ebi.ac.uk/biosamples/samples/")

    with pytest.raises(RuntimeError):
        bs_record.update_remote_record(get_header("thisIsAFakeToken"))
