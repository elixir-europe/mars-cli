import copy
import json
from configparser import ConfigParser
from unittest import mock

import pytest
from jsonschema.exceptions import ValidationError

from mars_lib.biosamples_external_references import (
    BiosamplesRecord,
    biosamples_endpoints,
    build_external_references,
    input_json_schema_filepath,
    load_json_file,
    handle_input_dict,
    get_header,
    validate_bs_accession,
    validate_json_against_schema,
)
from mars_lib.isa_json import load_investigation, update_investigation
from mars_lib.models.isa_json import Comment
from mars_lib.models.repository_response import RepositoryResponse
from mars_lib.submit import (
    create_external_references,
    register_external_references_in_biosamples,
)
from mars_lib.target_repo import TargetRepository

BS_FIXTURE = "./tests/fixtures/SAMEA112654119.json"
EXT_REFS_INPUT = "./tests/test_input.json"
DEV_URLS = TargetRepository.get_repository_urls_from_config(ConfigParser())["DEV"]
ENA_STUDY_PAGE = "https://wwwdev.ebi.ac.uk/ena/browser/view/"
MTBLS_STUDY_PAGE = "https://www-test.ebi.ac.uk/metabolights/"
# Sample 'leaf 1', the one used in the ENA assay of the fixture below
LEAF_ACCESSION = "SAMEA131504584"


def _investigation_after_ena_and_metabolights():
    """ISA-JSON as `submission` leaves it after BioSamples, ENA and MetaboLights."""
    investigation = update_investigation(
        load_investigation("tests/fixtures/isa_jsons/1_after_biosamples.json"),
        RepositoryResponse.from_json_file(
            "tests/fixtures/mars_receipts/ena_success_response.json"
        ),
    )
    study = investigation.studies[0]
    mtbls_assay = study.assays[0].model_copy(deep=True)
    mtbls_assay.id = "#assay/mtbls"
    mtbls_assay.comments = [Comment(name="target_repository", value="metabolights")]
    study.assays.append(mtbls_assay)
    study.comments.append(Comment(name="metabolights_accession", value="MTBLS1234"))
    return investigation


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


def test_create_external_references():
    endpoint = "https://wwwdev.ebi.ac.uk/biosamples/samples/"
    token_url = "https://wwwdev.ebi.ac.uk/ena/dev/submit/webin/auth/token"

    def fake_get(url, headers=None):
        if "SAMEA112654119" in url:
            return _response(status_code=404, text="Not found")
        return _response(payload={"accession": "SAMEA419425", "name": "no refs yet"})

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
        with pytest.raises(RuntimeError, match="SAMEA112654119") as error:
            create_external_references(
                {"username": "Webin-00000", "password": "secret"},
                load_json_file(EXT_REFS_INPUT),
                biosamples_url=endpoint.rstrip("/"),
                webin_token_url=token_url,
            )

    assert "SAMEA419425" not in str(error.value)
    assert post.call_args.args[0].startswith(token_url)
    # Records are fetched with the token, so private samples can be updated too
    assert all(
        c.kwargs["headers"] == get_header("the-token") for c in get.call_args_list
    )
    # The record that could not be fetched does not stop the next one
    put.assert_called_once()
    assert put.call_args.args[0] == f"{endpoint}SAMEA419425?authProvider=WEBIN"
    assert json.loads(put.call_args.kwargs["data"])["externalReferences"] == [
        {"url": "https://ega-archive.org/datasets/EGAD00010002458"}
    ]


def test_build_external_references():
    investigation = _investigation_after_ena_and_metabolights()

    ext_refs = build_external_references(
        investigation, {"ena": ENA_STUDY_PAGE, "metabolights": MTBLS_STUDY_PAGE}
    )

    # Only the sample used in the assays is linked, not the source it derives from
    assert ext_refs == {
        "biosampleExternalReferences": [
            {
                "biosampleAccession": LEAF_ACCESSION,
                "externalReferences": [
                    {"url": f"{ENA_STUDY_PAGE}ERP208805"},
                    {"url": f"{MTBLS_STUDY_PAGE}MTBLS1234"},
                ],
            }
        ]
    }
    assert validate_json_against_schema(ext_refs, input_json_schema_filepath)

    # Repositories that were not submitted to in this run are not linked
    ena_only = build_external_references(investigation, {"ena": ENA_STUDY_PAGE})
    assert ena_only["biosampleExternalReferences"][0]["externalReferences"] == [
        {"url": f"{ENA_STUDY_PAGE}ERP208805"}
    ]


@pytest.mark.parametrize("put_status, log_level", [(200, "info"), (403, "warning")])
def test_register_external_references_in_biosamples(put_status, log_level):
    with (
        mock.patch(
            "mars_lib.authentication.requests.post",
            return_value=_response(text="the-token"),
        ) as post,
        mock.patch(
            "mars_lib.biosamples_external_references.requests.get",
            return_value=_response(payload={"accession": LEAF_ACCESSION}),
        ),
        mock.patch(
            "mars_lib.biosamples_external_references.requests.put",
            return_value=_response(status_code=put_status, text="{}"),
        ) as put,
        mock.patch("mars_lib.submit.print_and_log") as log,
    ):
        # A failing BioSamples update must not fail the finished submission
        register_external_references_in_biosamples(
            investigation=_investigation_after_ena_and_metabolights(),
            user_credentials={"webin": {"username": "Webin-0", "password": "pw"}},
            study_page_urls={"ena": DEV_URLS["ENA"]["STUDY-PAGE"]},
            urls=DEV_URLS,
        )

    assert post.call_args.args[0].startswith(DEV_URLS["WEBIN"]["TOKEN"])
    assert put.call_args.args[0].startswith(
        f"{DEV_URLS['BIOSAMPLES']['SERVICE']}{LEAF_ACCESSION}"
    )
    assert json.loads(put.call_args.kwargs["data"])["externalReferences"] == [
        {"url": f"{ENA_STUDY_PAGE}ERP208805"}
    ]
    assert log.call_args.kwargs["level"] == log_level


def test_update_remote_record():
    bs_record = BiosamplesRecord("SAMEA112654119")
    bs_record.fetch_bs_json("https://wwwdev.ebi.ac.uk/biosamples/samples/")

    with pytest.raises(RuntimeError):
        bs_record.update_remote_record(get_header("thisIsAFakeToken"))
