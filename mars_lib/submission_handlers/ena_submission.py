from click import Path

import requests

from mars_lib.ftp_upload import FTPUploader
from mars_lib.isa_json import reduce_isa_json_for_target_repo
from mars_lib.models.isa_json import IsaJson
from mars_lib.target_repo import TargetRepository
from typing import List

def submit_to_ena(
    isa_json: IsaJson, user_credentials: dict[str, str], submission_url: str
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
        json=reduce_isa_json_for_target_repo(
            isa_json, TargetRepository.ENA.value
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
        f"Request towards ENA failed!\nRequest:\nMethod:{result.request.method}\nStatus:{result.status_code}\nURL:{submission_url}\nParams: ['webinUserName': {params.get('webinUserName')}, 'webinPassword': ****]\nHeaders:{result.request.headers}\nBody:{body}"
    )


def upload_to_ena(
    file_paths: List[Path],
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