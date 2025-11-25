from mars_lib.models.isa_json import IsaJson
import requests
from mars_lib.isa_json import reduce_isa_json_for_target_repo
from mars_lib.authentication import get_webin_auth_token
from mars_lib.target_repo import TargetRepository

def submit_to_biosamples(
    isa_json: IsaJson,
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
        json=reduce_isa_json_for_target_repo(
            isa_json, TargetRepository.BIOSAMPLES.value
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
