import os
from datetime import datetime
from io import TextIOWrapper
import json
from typing import Any
from mars_lib.authentication import (
    get_webin_auth_token,
    load_credentials,
    AuthProvider,
)
from mars_lib.credential import CredentialManager
from mars_lib.isa_json import (
    load_isa_json,
    update_isa_json,
    map_data_files_to_repositories,
)
from mars_lib.models.isa_json import Comment, IsaJson
from mars_lib.models.repository_response import RepositoryResponse
from mars_lib.target_repo import TargetRepository
from mars_lib.logging import print_and_log
from pydantic import ValidationError

from pathlib import Path
from typing import List

from mars_lib.submission_handlers.biosamples_submission import submit_to_biosamples
from mars_lib.submission_handlers.ena_submission import submit_to_ena, upload_to_ena
from mars_lib.submission_handlers.metabolights_submission import upload_to_metabolights
from mars_lib.biosamples_external_references import update_external_references

def save_step_to_file(time_stamp: float, filename: str, isa_json: IsaJson):
    dir_path = f"tmp/{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}"
    os.makedirs(dir_path, exist_ok=True)

    with open(f"{dir_path}/{filename}.json", "w") as f:
        f.write(isa_json.model_dump_json(by_alias=True, exclude_none=True))


DEBUG = os.getenv("MARS_DEBUG") in ["1", 1]


def submission(
    webin_username: str,
    metabolights_username: str,
    metabolights_ftp_username: str,
    credentials_file: TextIOWrapper,
    isa_json_file: str,
    target_repositories: list[str],
    investigation_is_root: bool,
    urls: dict[str, Any],
    file_transfer: str,
    output: str,
    data_file_paths: List[TextIOWrapper],
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

    isa_json = load_isa_json(isa_json_file, investigation_is_root)

    # Guard clause to keep MyPy happy
    if isinstance(isa_json, ValidationError):
        raise ValidationError(f"ISA JSON is invalid: {isa_json}")

    print_and_log(
        f"ISA JSON with investigation '{isa_json.investigation.title}' is valid."
    )

    # create data file map
    data_file_map = map_data_files_to_repositories(
        files=[str(dfp) for dfp in data_file_paths], isa_json=isa_json
    )

    time_stamp = datetime.timestamp(datetime.now())

    if DEBUG:
        save_step_to_file(time_stamp, "0_Initial_ISA_JSON_in_model", isa_json)

    if all(
        repo not in TargetRepository.available_repositories()
        for repo in target_repositories
    ):
        raise ValueError("No target repository selected.")

    if TargetRepository.BIOSAMPLES.value in target_repositories:
        # Submit to Biosamples
        biosamples_result = submit_to_biosamples(
            isa_json=isa_json,
            biosamples_credentials=user_credentials[AuthProvider.WEBIN.value],
            biosamples_url=urls["BIOSAMPLES"]["SUBMISSION"],
            webin_token_url=urls["WEBIN"]["TOKEN"],
        )
        print_and_log(
            f"Submission to {TargetRepository.BIOSAMPLES.value} was successful. Result:\n{biosamples_result.json()}",
            level="info",
        )
        # Update `isa_json`, based on the receipt returned
        bs_mars_receipt = RepositoryResponse.model_validate(
            json.loads(biosamples_result.content)
        )
        isa_json = update_isa_json(isa_json, bs_mars_receipt)
        if DEBUG:
            save_step_to_file(time_stamp, "1_after_biosamples", isa_json)

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
            isa_json=isa_json,
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
        isa_json = update_isa_json(isa_json, ena_mars_receipt)
        if DEBUG:
            save_step_to_file(time_stamp, "2_after_ena", isa_json)

    if TargetRepository.METABOLIGHTS.value in target_repositories:
        # Submit to MetaboLights
        metabolights_result = upload_to_metabolights(
            file_paths=data_file_map[TargetRepository.METABOLIGHTS.value],
            file_transfer=file_transfer,
            isa_json=isa_json,
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
        isa_json.investigation.studies[0].comments.append(
            Comment(
                name="metabolights_accession",
                value=metabolights_receipt.accessions[0].value,
            )
        )
        if DEBUG:
            save_step_to_file(time_stamp, "3_after_metabolights", isa_json)

    if TargetRepository.EVA.value in target_repositories:
        # Submit to EVA
        # TODO: Filter out other assays
        print_and_log(
            f"Submission to {TargetRepository.EVA.value} is currently not supported and is skipped.", level="info"
        )
        # TODO: Update `isa_json`, based on the receipt returned

    # Submitting the external references to Biosamples
    update_external_references(isa_json, urls)

    # Return the updated ISA JSON
    with open(f"{output}.json", "w") as f:
        f.write(isa_json.model_dump_json(by_alias=True, exclude_none=True))
