import copy
import json
import os
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


def test_input_schema_is_found_from_any_working_directory(tmp_path, monkeypatch):
    assert os.path.isabs(input_json_schema_filepath)
    assert os.path.isfile(input_json_schema_filepath)

    ext_refs = load_json_file(EXT_REFS_INPUT)
    monkeypatch.chdir(tmp_path)
    assert validate_json_against_schema(ext_refs, input_json_schema_filepath)


def test_validate_json_against_schema():
    assert validate_json_against_schema(EXT_REFS_INPUT, input_json_schema_filepath)

    with_duo = {
        "biosampleExternalReferences": [
            {
                "biosampleAccession": "SAMEA112654119",
                "externalReferences": [
                    {"url": "https://example.org/1", "duo": ["DUO:0000005"]}
                ],
            }
        ]
    }
    assert validate_json_against_schema(with_duo, input_json_schema_filepath)


def _single_ref_input(accession="SAMEA112654119", ext_ref=None):
    return {
        "biosampleExternalReferences": [
            {
                "biosampleAccession": accession,
                "externalReferences": [ext_ref or {"url": "https://example.org/1"}],
            }
        ]
    }


@pytest.mark.parametrize(
    "invalid_input",
    [
        {},
        {"biosampleExternalReferences": []},
        _single_ref_input(accession="SAREA112654119"),
        _single_ref_input(ext_ref={"duo": []}),
        _single_ref_input(ext_ref={"url": "https://example.org/1", "duo": ["5"]}),
        _single_ref_input(ext_ref={"url": "https://example.org/1", "foo": "bar"}),
        {
            "biosampleExternalReferences": [
                {"biosampleAccession": "SAMEA112654119", "externalReferences": []}
            ]
        },
    ],
    ids=[
        "missing-root-key",
        "no-samples",
        "bad-accession",
        "missing-url",
        "bad-duo-code",
        "unknown-ref-property",
        "no-refs",
    ],
)
def test_validate_json_against_schema_rejects_invalid_input(invalid_input):
    with pytest.raises(ValidationError):
        validate_json_against_schema(invalid_input, input_json_schema_filepath)


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
            # Same reference, different key order
            {"duo": [], "url": "https://www.ebi.ac.uk/ena/browser/view/SAMEA112654119"},
            {"url": "https://example.org/new", "duo": ["DUO:0000007", "DUO:0000005"]},
            # Same as the previous one, DUO codes in another order
            {"url": "https://example.org/new", "duo": ["DUO:0000005", "DUO:0000007"]},
            # Same url, different DUO codes: a different reference for BioSamples
            {"url": "https://example.org/new", "duo": ["DUO:0000042"]},
        ]
    )

    # Existing references keep their order, new ones are appended once
    assert bs_record.bs_json["externalReferences"] == existing_refs + [
        {"url": "https://example.org/new", "duo": ["DUO:0000007", "DUO:0000005"]},
        {"url": "https://example.org/new", "duo": ["DUO:0000042"]},
    ]
    assert "_links" not in bs_record.bs_json


def test_extend_externalReferences_on_record_without_refs():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.load_bs_json({"accession": "SAMEA112654119", "name": "sample"})

    new_refs = [{"url": "https://example.org/1"}, {"url": "https://example.org/1"}]
    bs_record.extend_externalReferences(new_refs)

    assert bs_record.bs_json["externalReferences"] == [{"url": "https://example.org/1"}]


@pytest.mark.parametrize("production", [False, True])
def test_extend_externalReferences_fetches_record_when_not_loaded(production):
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.production = production
    record = load_json_file(BS_FIXTURE)

    with mock.patch(
        "mars_lib.biosamples_external_references.requests.get",
        return_value=_response(payload=record),
    ) as get:
        bs_record.extend_externalReferences([{"url": "https://example.org/1"}])

    endpoint = biosamples_endpoints["prod" if production else "dev"]
    assert get.call_args.args[0] == f"{endpoint}SAMEA112654119.json"
    assert {"url": "https://example.org/1"} in bs_record.bs_json["externalReferences"]


def test_fetch_bs_json_sends_header():
    bs_record = BiosamplesRecord("SAMEA112654119")
    header = get_header("token")

    with mock.patch(
        "mars_lib.biosamples_external_references.requests.get",
        return_value=_response(payload={"accession": "SAMEA112654119"}),
    ) as get:
        bs_record.fetch_bs_json(biosamples_endpoints["dev"], header=header)

    assert get.call_args.kwargs["headers"] == header


def test_update_remote_record_after_load_bs_json():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.load_bs_json(BS_FIXTURE)
    bs_record.extend_externalReferences([{"url": "https://example.org/1"}])
    header = get_header("token")

    with mock.patch(
        "mars_lib.biosamples_external_references.requests.put",
        return_value=_response(text="{}"),
    ) as put:
        bs_record.update_remote_record(header)

    assert (
        put.call_args.args[0]
        == f"{biosamples_endpoints['dev']}SAMEA112654119?authProvider=WEBIN"
    )
    assert put.call_args.kwargs["headers"] == header
    assert json.loads(put.call_args.kwargs["data"]) == bs_record.bs_json


def test_update_remote_record_raises_on_error_status():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.load_bs_json(BS_FIXTURE)

    with mock.patch(
        "mars_lib.biosamples_external_references.requests.put",
        return_value=_response(status_code=403, text="Forbidden"),
    ):
        with pytest.raises(RuntimeError, match="received 403"):
            bs_record.update_remote_record(get_header("token"))


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("from_file", [False, True])
def test_create_external_references(production, from_file):
    environment = "prod" if production else "dev"
    endpoint = biosamples_endpoints[environment]
    credentials = {"username": "Webin-00000", "password": "secret"}
    records = {
        "SAMEA112654119": load_json_file(BS_FIXTURE),
        "SAMEA419425": {"accession": "SAMEA419425", "name": "no refs yet"},
    }

    def fake_get(url, headers=None):
        accession = url.removeprefix(endpoint).removesuffix(".json")
        return _response(payload=copy.deepcopy(records[accession]))

    ext_refs_input = EXT_REFS_INPUT if from_file else load_json_file(EXT_REFS_INPUT)

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
        create_external_references(credentials, ext_refs_input, production)

    # Token is requested from the Webin instance matching the BioSamples instance
    assert post.call_args.args[0].startswith(webin_token_endpoints[environment])

    expected_header = get_header("the-token")
    assert [c.args[0] for c in get.call_args_list] == [
        f"{endpoint}SAMEA112654119.json",
        f"{endpoint}SAMEA419425.json",
    ]
    assert all(c.kwargs["headers"] == expected_header for c in get.call_args_list)

    assert [c.args[0] for c in put.call_args_list] == [
        f"{endpoint}SAMEA112654119?authProvider=WEBIN",
        f"{endpoint}SAMEA419425?authProvider=WEBIN",
    ]
    assert all(c.kwargs["headers"] == expected_header for c in put.call_args_list)

    first_update, second_update = (
        json.loads(c.kwargs["data"]) for c in put.call_args_list
    )
    # All three input refs are already on SAMEA112654119, so nothing is added
    assert (
        first_update["externalReferences"]
        == records["SAMEA112654119"]["externalReferences"]
    )
    assert "_links" not in first_update
    assert second_update["externalReferences"] == [
        {"url": "https://ega-archive.org/datasets/EGAD00010002458"}
    ]


def test_create_external_references_rejects_invalid_input_before_any_request():
    with (
        mock.patch("mars_lib.authentication.requests.post") as post,
        mock.patch("mars_lib.biosamples_external_references.requests.get") as get,
        mock.patch("mars_lib.biosamples_external_references.requests.put") as put,
    ):
        with pytest.raises(ValidationError):
            create_external_references(
                {"username": "Webin-00000", "password": "secret"},
                _single_ref_input(accession="SAREA112654119"),
                production=False,
            )

    post.assert_not_called()
    get.assert_not_called()
    put.assert_not_called()


def test_update_remote_record():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.fetch_bs_json("https://wwwdev.ebi.ac.uk/biosamples/samples/")

    with pytest.raises(RuntimeError):
        bs_record.update_remote_record(get_header("thisIsAFakeToken"))
