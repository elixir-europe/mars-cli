import io
import os
from datetime import datetime
from io import TextIOWrapper
import time
import requests
import json
from typing import Any
from mars_lib.authentication import (
    get_metabolights_auth_token,
    get_webin_auth_token,
    load_credentials,
    AuthProvider,
)
from mars_lib.biosamples_external_references import (
    get_header,
    BiosamplesRecord,
    build_external_references,
    handle_input_dict,
    validate_json_against_schema,
    input_json_schema_filepath,
)
from mars_lib.credential import CredentialManager
from mars_lib.isa_json import (
    check_data_files,
    load_investigation,
    reduce_investigation_for_target_repo,
    update_investigation,
    map_data_files_to_repositories,
)
from mars_lib.models.isa_json import Comment, Investigation
from mars_lib.models.repository_response import RepositoryResponse
from mars_lib.target_repo import TargetRepository
from mars_lib.logging import print_and_log
from pydantic import ValidationError

from mars_lib.ftp_upload import FTPUploader
from pathlib import Path


def save_step_to_file(time_stamp: float, filename: str, investigation: Investigation):
    dir_path = f"tmp/{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}"
    os.makedirs(dir_path, exist_ok=True)

    with open(f"{dir_path}/{filename}.json", "w") as f:
        f.write(investigation.model_dump_json(by_alias=True, exclude_none=True))


DEBUG = os.getenv("MARS_DEBUG") in ["1", 1]


def submission(
    webin_username: str,
    metabolights_username: str,
    metabolights_ftp_username: str,
    credentials_file: TextIOWrapper,
    isa_json_file: str,
    target_repositories: list[str],
    urls: dict[str, Any],
    file_transfer: str,
    output: str,
    data_file_paths: list[str] = [],
) -> None:
    # If credential manager info found:
    # Get password from the credential manager
    # Else:
    # read credentials from file
    if all([webin_username, metabolights_username, metabolights_ftp_username]):
        user_credentials = {
            cred_pair[0]: {
                "username": cred_pair[1],
                "password": CredentialManager(cred_pair[0]).get_password_keyring(
                    cred_pair[1]
                ),
            }
            for cred_pair in zip(
                AuthProvider.available_providers(),
                [webin_username, metabolights_username, metabolights_ftp_username],
            )
        }
    else:
        if credentials_file == "":
            raise ValueError("No credentials found")

        user_credentials = load_credentials(credentials_file)

    investigation = load_investigation(isa_json_file)

    # Guard clause to keep MyPy happy
    if isinstance(investigation, ValidationError):
        raise ValidationError(f"ISA JSON is invalid: {investigation}")

    print_and_log(f"ISA JSON with investigation '{investigation.title}' is valid.")

    # Check that every data file in the ISA-JSON is matched by a passed file
    data_file_report = check_data_files(
        [str(dfp) for dfp in data_file_paths], investigation
    )
    data_file_report.log()
    if data_file_report.missing:
        raise ValueError(
            "Data files referenced in the ISA JSON are missing from the data files "
            f"passed in the command: {', '.join(data_file_report.missing)}"
        )
    if data_file_report.duplicates:
        raise ValueError(
            "Multiple data files share the name of a data file in the ISA JSON: "
            f"{', '.join(data_file_report.duplicates)}"
        )

    # create data file map
    data_file_map = map_data_files_to_repositories(
        files=[str(dfp) for dfp in data_file_paths], investigation=investigation
    )

    time_stamp = datetime.timestamp(datetime.now())

    if DEBUG:
        save_step_to_file(time_stamp, "0_Initial_ISA_JSON_in_model", investigation)

    if all(
        repo not in TargetRepository.available_repositories()
        for repo in target_repositories
    ):
        raise ValueError("No target repository selected.")

    # Abort before submitting anything when a target repository has nothing to receive
    for repo in target_repositories:
        reduce_investigation_for_target_repo(investigation, repo)

    if TargetRepository.BIOSAMPLES.value in target_repositories:
        # Submit to Biosamples
        biosamples_result = submit_to_biosamples(
            investigation=investigation,
            biosamples_credentials=user_credentials[AuthProvider.WEBIN.value],
            biosamples_url=urls["BIOSAMPLES"]["SUBMISSION"],
            webin_token_url=urls["WEBIN"]["TOKEN"],
        )
        print_and_log(
            f"Submission to {TargetRepository.BIOSAMPLES.value} was successful. Result:\n{biosamples_result.json()}",
            level="info",
        )
        # Update `investigation`, based on the receipt returned
        bs_mars_receipt = RepositoryResponse.model_validate(
            json.loads(biosamples_result.content)
        )
        investigation = update_investigation(investigation, bs_mars_receipt)
        if DEBUG:
            save_step_to_file(time_stamp, "1_after_biosamples", investigation)

    if TargetRepository.ENA.value in target_repositories:
        # Step 1 : upload data if file paths are provided
        if data_file_paths and file_transfer:
            upload_to_ena(
                file_paths=[
                    Path(df) for df in data_file_map[TargetRepository.ENA.value]
                ],
                user_credentials=user_credentials[AuthProvider.WEBIN.value],
                submission_url=urls["ENA"]["DATA-SUBMISSION"],
                file_transfer=file_transfer,
            )
        print_and_log(
            f"Start submitting to {TargetRepository.ENA.value}.", level="debug"
        )

        # Step 2 : submit isa-json to ena
        ena_result = submit_to_ena(
            investigation=investigation,
            user_credentials=user_credentials[AuthProvider.WEBIN.value],
            submission_url=urls["ENA"]["SUBMISSION"],
        )
        print_and_log(
            f"Submission to {TargetRepository.ENA.value} was successful. Result:\n{ena_result.json()}"
        )

        print_and_log(
            f"Update ISA-JSON based on receipt from {TargetRepository.ENA.value}.",
            level="debug",
        )
        ena_mars_receipt = RepositoryResponse.model_validate(
            json.loads(ena_result.content)
        )
        investigation = update_investigation(investigation, ena_mars_receipt)
        if DEBUG:
            save_step_to_file(time_stamp, "2_after_ena", investigation)

    if TargetRepository.METABOLIGHTS.value in target_repositories:
        # Submit to MetaboLights
        metabolights_result = upload_to_metabolights(
            file_paths=data_file_map[TargetRepository.METABOLIGHTS.value],
            file_transfer=file_transfer,
            investigation=investigation,
            metabolights_credentials=user_credentials[
                AuthProvider.METABOLIGHTS_METADATA.value
            ],
            metabolights_url=urls["METABOLIGHTS"]["SUBMISSION"],
            metabolights_token_url=urls["METABOLIGHTS"]["TOKEN"],
        )
        metabolights_receipt_obj = metabolights_result.json()
        print_and_log(
            f"Submission to {TargetRepository.METABOLIGHTS.value} was successful. Result:\n{metabolights_receipt_obj}",
            level="info",
        )
        metabolights_receipt = RepositoryResponse.model_validate(
            metabolights_receipt_obj
        )
        # TODO: MetaboLights creates accession number with errors. Errors are not handled.
        investigation.studies[0].comments.append(
            Comment(
                name="metabolights_accession",
                value=metabolights_receipt.accessions[0].value,
            )
        )
        if DEBUG:
            save_step_to_file(time_stamp, "3_after_metabolights", investigation)

    if TargetRepository.EVA.value in target_repositories:
        # Submit to EVA
        # TODO: Filter out other assays
        print_and_log(
            f"Submission to {TargetRepository.EVA.value} was successful.", level="info"
        )
        # TODO: Update `investigation`, based on the receipt returned

    # Link the new repository studies to the BioSamples records they were made from
    study_page_urls = {
        repo.value: urls[repo.name]["STUDY-PAGE"]
        for repo in (TargetRepository.ENA, TargetRepository.METABOLIGHTS)
        if repo.value in target_repositories
    }
    if study_page_urls:
        register_external_references_in_biosamples(
            investigation=investigation,
            user_credentials=user_credentials,
            study_page_urls=study_page_urls,
            urls=urls,
        )

    # Return the updated ISA JSON
    with open(f"{output}.json", "w") as f:
        f.write(investigation.model_dump_json(by_alias=True, exclude_none=True))


def submit_to_biosamples(
    investigation: Investigation,
    biosamples_credentials: dict[str, str],
    webin_token_url: str,
    biosamples_url: str,
) -> requests.Response:
    params = {
        "webinjwt": get_webin_auth_token(
            biosamples_credentials, auth_base_url=webin_token_url
        )
    }
    headers = {"accept": "*/*", "Content-Type": "application/json"}
    result = requests.post(
        biosamples_url,
        headers=headers,
        params=params,
        json=reduce_investigation_for_target_repo(
            investigation, TargetRepository.BIOSAMPLES.value
        ).model_dump(by_alias=True, exclude_none=True),
    )

    if result.status_code == 200 and not result.json().get("errors", []):
        return result

    body = (
        result.request.body.decode()
        if isinstance(result.request.body, bytes)
        else result.request.body or ""
    )
    raise requests.HTTPError(
        f"Request towards BioSamples failed!\nRequest:\nMethod:{result.request.method}\nStatus:{result.status_code}\nURL:{result.request.url}\nHeaders:{result.request.headers}\nBody:{body}"
    )


def upload_to_metabolights(
    file_paths: list[str],
    investigation: Investigation,
    metabolights_credentials: dict[str, str],
    metabolights_url: str,
    metabolights_token_url: str,
    file_transfer: str = "ftp",
):
    data_upload_protocol = (
        "ftp" if not file_transfer or file_transfer.lower() == "ftp" else ""
    )

    if not data_upload_protocol == "ftp":
        raise ValueError(
            f"Data upload protocol {data_upload_protocol} is not supported"
        )

    token = get_metabolights_auth_token(
        metabolights_credentials, auth_url=metabolights_token_url
    )
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    investigation_str = reduce_investigation_for_target_repo(
        investigation, TargetRepository.METABOLIGHTS.value
    ).model_dump_json(by_alias=True, exclude_none=True)
    json_file = io.StringIO(investigation_str)

    files = {"isa_json_file": ("isa_json.json", json_file)}
    result = None
    try:
        submission_response = requests.post(
            metabolights_url,
            headers=headers,
            files=files,
            timeout=120,
        )
        submission_response.raise_for_status()
        if submission_response.json().get("errors", []):
            response_body = submission_response.request.body
            if isinstance(response_body, bytes):
                response_body = response_body.decode("utf-8")
            raise requests.HTTPError(
                f"Request towards MetaboLights failed!\nRequest:\nMethod:{submission_response.request.method}\nStatus:{submission_response.status_code}\nURL:{submission_response.request.url}\nHeaders:{submission_response.request.headers}\nBody:{response_body}"
            )

        result = submission_response.json()
    except Exception as exc:
        raise exc

    validation_url = find_value_in_info_section("validation-url", result["info"])
    validation_status_url = find_value_in_info_section(
        "validation-status-url", result["info"]
    )
    ftp_credentials_url = find_value_in_info_section(
        "ftp-credentials-url", result["info"]
    )

    if file_transfer == "ftp":
        ftp_credentials_response = requests.get(ftp_credentials_url, headers=headers)
        ftp_credentials_response.raise_for_status()
        ftp_credentials = ftp_credentials_response.json()
        ftp_base_path = ftp_credentials["ftpPath"]  # noqa F841
        uploader = FTPUploader(  # noqa F841
            ftp_credentials["ftpHost"],
            ftp_credentials["ftpUser"],
            ftp_credentials["ftpPassword"],
        )
        # TODO: Update after the uploader is implemented/tested
        # uploader.upload(file_paths, target_location=ftp_base_path)

    validation_response = requests.post(validation_url, headers=headers)
    validation_response.raise_for_status()
    pool_time_in_seconds = 10
    max_pool_count = 100
    validation_status_response = None
    for _ in range(max_pool_count):
        timeout = False
        try:
            validation_status_response = requests.get(
                validation_status_url, headers=headers, timeout=30
            )
            validation_status_response.raise_for_status()
        except requests.exceptions.Timeout:
            timeout = True
        if not timeout:
            if validation_status_response is None:
                raise ValueError("Validation status response is None")
            validation_status = validation_status_response.json()
            validation_time = find_value_in_info_section(
                "validation-time", validation_status["info"], fail_gracefully=True
            )
            if validation_time:
                break
        time.sleep(pool_time_in_seconds)
    else:
        raise ValueError(f"Validation failed after {max_pool_count} iterations")

    if validation_status_response:
        return validation_status_response

    raise ValueError("Submission failed for MetaboLights")


def find_value_in_info_section(
    key: str, info_section: list[Any], fail_gracefully: bool = False
) -> Any:
    for info in info_section:
        if info["name"] == key:
            return info["message"]
    if fail_gracefully:
        return None
    raise ValueError(f"Name {key} not found in info section")


def submit_to_ena(
    investigation: Investigation, user_credentials: dict[str, str], submission_url: str
) -> requests.Response:
    params = {
        "webinUserName": user_credentials["username"],
        "webinPassword": user_credentials["password"],
    }
    headers = {"accept": "*/*", "Content-Type": "application/json"}
    result = requests.post(
        submission_url,
        headers=headers,
        params=params,
        json=reduce_investigation_for_target_repo(
            investigation, TargetRepository.ENA.value
        ).model_dump(by_alias=True, exclude_none=True),
    )

    if result.status_code == 200 and not result.json().get("errors", []):
        return result

    response_body = ""
    try:
        response_body = json.dumps(result.json(), indent=2)
    except Exception:
        response_body = result.text

    body = (
        result.request.body.decode()
        if isinstance(result.request.body, bytes)
        else result.request.body or ""
    )
    raise requests.HTTPError(
        f"Request towards ENA failed!\nRequest:\nMethod:{result.request.method}\nStatus:{result.status_code}\nURL:{submission_url}\nParams: ['webinUserName': {params.get('webinUserName')}, 'webinPassword': ****]\nHeaders:{result.request.headers}\nBody:{body}\nResponse:\n{response_body}"
    )


def upload_to_ena(
    file_paths: list[Path],
    user_credentials: dict[str, str],
    submission_url: str,
    file_transfer: str,
):
    file_transfer = file_transfer.lower()

    if file_transfer == "ftp":
        uploader = FTPUploader(
            submission_url,
            user_credentials["username"],
            user_credentials["password"],
        )
        uploader.upload(file_paths)


def create_external_references(
    biosamples_credentials: dict[str, str],
    biosamples_externalReferences: dict[str, Any] | str,
    biosamples_url: str,
    webin_token_url: str,
) -> None:
    """
    Adds externalReferences to existing BioSamples records.
        Every record is attempted; the ones that could not be updated are reported together afterwards.

    Args:
    biosamples_credentials: Dictionary with the credentials of the submitter of the existing Biosamples records.
    biosamples_externalReferences: Dictionary, or path to a JSON file, mapping BioSamples accessions to the
        externalReferences to add. Must follow 'biosamples-input-schema.json'.
    biosamples_url: BioSamples samples endpoint, e.g. 'https://wwwdev.ebi.ac.uk/biosamples/samples/'.
    webin_token_url: Webin URL to request the authentication token from.
    """
    biosamples_endpoint = f"{biosamples_url.rstrip('/')}/"

    ext_refs_input = handle_input_dict(biosamples_externalReferences)
    validate_json_against_schema(
        json_doc=ext_refs_input, json_schema=input_json_schema_filepath
    )
    token = get_webin_auth_token(biosamples_credentials, auth_base_url=webin_token_url)
    if not token:
        raise ValueError("The token could not be generated.")
    header = get_header(token)

    failures = []
    for biosample_r in ext_refs_input["biosampleExternalReferences"]:
        bs_accession = biosample_r["biosampleAccession"]
        try:
            BSrecord = BiosamplesRecord(bs_accession)
            BSrecord.fetch_bs_json(biosamples_endpoint, header=header)
            BSrecord.extend_externalReferences(biosample_r["externalReferences"])
            BSrecord.update_remote_record(header)
        except (RuntimeError, ValueError) as err:
            failures.append(f"{bs_accession}: {err}")

    if failures:
        raise RuntimeError(
            "Could not add the external references to these BioSamples records:\n"
            + "\n".join(failures)
        )


def register_external_references_in_biosamples(
    investigation: Investigation,
    user_credentials: dict[str, dict[str, str]],
    study_page_urls: dict[str, str],
    urls: dict[str, Any],
) -> None:
    """
    Adds a link to the repository study of each assay to the BioSamples records of the samples in that assay.
        The submission itself already succeeded at this point, so problems are logged as a warning instead of raised.

    Args:
    investigation: ISA-JSON, updated with the receipts of the target repositories.
    user_credentials: Credentials per authentication provider. Uses the Webin ones.
    study_page_urls: Target repository mapped to the base URL of its study pages.
    urls: Repository URLs of the selected environment.
    """
    try:
        ext_refs = build_external_references(investigation, study_page_urls)
        if not ext_refs["biosampleExternalReferences"]:
            print_and_log(
                f"No BioSamples records to link to the {', '.join(study_page_urls)} studies: "
                "the submitted assays have no study accession or no samples with a BioSamples accession.",
                level="warning",
            )
            return

        create_external_references(
            biosamples_credentials=user_credentials[AuthProvider.WEBIN.value],
            biosamples_externalReferences=ext_refs,
            biosamples_url=urls["BIOSAMPLES"]["SAMPLES"],
            webin_token_url=urls["WEBIN"]["TOKEN"],
        )
    except Exception as err:
        print_and_log(
            f"The submission succeeded, but the BioSamples records could not be linked to it. Details:\n{err}",
            level="warning",
        )
        return

    print_and_log(
        f"Linked the {', '.join(study_page_urls)} studies to "
        f"{len(ext_refs['biosampleExternalReferences'])} BioSamples record(s).",
        level="info",
    )
