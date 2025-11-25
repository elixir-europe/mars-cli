import io
import time
import requests
from typing import Any
from mars_lib.authentication import get_metabolights_auth_token
from mars_lib.isa_json import reduce_isa_json_for_target_repo
from mars_lib.models.isa_json import IsaJson
from mars_lib.target_repo import TargetRepository


def upload_to_metabolights(
    file_paths: list[str],
    isa_json: IsaJson,
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
    isa_json_str = reduce_isa_json_for_target_repo(
        isa_json, TargetRepository.METABOLIGHTS
    ).investigation.model_dump_json(by_alias=True, exclude_none=True)
    json_file = io.StringIO(isa_json_str)

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
