import io
from pathlib import Path
import tempfile
from typing import Any
from urllib.parse import urlencode

import requests

from mars_lib.models.abstract_repository_client import (
    AuthError,
    BaseRepositoryClient,
    FtpCredentials,
    InitiationResponse,
    RepositoryConfig,
    RepositoryConfigError,
    RepositoryInitiationError,
    SubmissionContext,
    RepositorySubmissionError,
    Token,
)
from mars_lib.models.isa_json import Investigation
from mars_lib.models.repository_response import RepositoryResponse
from mtblspy.commands.submissions.client import SubmissionClient
from mtblspy.commands.submissions.models import (
    StudyCreationRequest,
)


class MetaboLightsRepositoryClient(BaseRepositoryClient):
    def __init__(self, **kwargs):

        super().__init__(
            repository_name="metabolights",
            repository_config_key="metabolights",
            **kwargs,
        )

    def check_repository_config(
        self, repository_config: RepositoryConfig
    ) -> None | list[str]:
        metabolights_name = self.repository_name
        label = metabolights_name.lower()

        if not repository_config:
            return ["MetaboLights repository  configuration is not defined"]
        errors = []

        credentials = repository_config.credentials
        if not credentials:
            errors.append(f"Credentials are not defined for {label}")
        else:
            if not credentials.get("username"):
                errors.append(f"Username is not defined for {label}")
            if not credentials.get("password"):
                errors.append(f"Password not defined for {label}")

        settings = repository_config.settings
        if not settings:
            errors.append(f"Settings are not defined for {label}")
        else:
            if not settings.get("auth-url"):
                errors.append(f"auth-url is not defined for {label}")
            if not settings.get("auth-client-name"):
                errors.append(f"auth-client-name is not defined for {label}")
            # if not settings.get("submission-base-url"):
            #     errors.append(f"auth-submission-base-url is not defined for {label}")

        return errors or None

    def get_token(self, context: SubmissionContext) -> None | Token:
        credentials = context.repository_config.credentials
        settings = context.repository_config.settings

        username = credentials.get("username")
        password = credentials.get("password")
        auth_client_name = settings.get("auth-client-name")
        auth_url = settings.get("auth-url")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        form_data = urlencode(
            {
                "grant_type": "password",
                "scope": "openid profile email",
                "username": username,
                "password": password,
                "client_id": auth_client_name,
            }
        )

        response = requests.post(
            auth_url,
            headers=headers,
            data=form_data,
            timeout=20,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as ex:
            response_content = response.content.decode("utf-8")
            raise AuthError(
                "ERROR when generating MetaboLights token. "
                f"Status: {response.status_code}. Response content:\n{response_content}"
            ) from ex

        response_content = response.json()
        access_token = response_content.get("access_token")
        refresh_token = response_content.get("refresh_token")
        if access_token:
            return Token(token=access_token, refresh_token=refresh_token)
        else:
            error_message = f"ERROR when generating token. See response's content below:\n{response_content}"
            raise AuthError(error_message)

    def reduce_investigation(self, input_investigation: Investigation) -> Investigation:
        """
        Filters out assays that are not meant to be sent to MetaboLights.

        Args:
            input_investigation (Investigation): Input ISA JSON that contains the original information.

        Returns:
            Investigation: Filtered ISA JSON.
        """
        filtered_investigation = super().reduce_investigation(
            input_investigation=input_investigation
        )

        # Select only first study
        if len(filtered_investigation.studies) > 1:
            filtered_investigation.studies = [filtered_investigation.studies[0]]

        return filtered_investigation

    def initiate_submission(
        self, context: SubmissionContext, repository_token: None | Token
    ) -> None | InitiationResponse:

        self._check_context(context)

        errors = self.check_repository_config(
            repository_config=context.repository_config
        )

        if errors:
            raise RepositoryInitiationError(
                f"repository configuration error: {','.join(errors)}"
            )

        if not repository_token or not repository_token.token:
            raise RepositorySubmissionError(
                "Token is required for MetaboLights Submission"
            )
        submission_id = context.submission_id
        settings = context.repository_config.settings
        base_url = settings.get("submission-base-url")
        client = SubmissionClient(base_url=base_url)
        if not submission_id:
            issuer = settings.get("auth-issuer")
            client = SubmissionClient(base_url=base_url)
            client.login_with_jwt(
                jwt_token=repository_token.token,
                refresh_token=repository_token.refresh_token,
                issuer=issuer,
            )
            study_category, assay_type = self._identify_study_category(context)
            new_submission_request = StudyCreationRequest(
                selected_study_categories={study_category: [assay_type]},
                title="MetaboLights study requested by MARS client",
                description="MetaboLights study requested by MARS client",
                selected_submission_workflows=["mars"],
                dataset_license_agreement=True,
                dataset_policy_agreement=True,
            )
            temp_file = tempfile.NamedTemporaryFile("w")
            try:
                temp_file.write(new_submission_request.model_dump_json(by_alias=True))
                temp_file.file.flush()

                response_json = client.create_study(input_file_path=temp_file.name)
                if response_json:
                    studies = response_json.get("studies", {})
                    if studies:
                        submission_id = list(studies.keys())[0]
            finally:
                if Path(temp_file.name).exists():
                    Path(temp_file.name).unlink()

            if not submission_id:
                raise RepositorySubmissionError(
                    "Submission id is not defined or created for the study"
                )

        details = client.get_private_ftp_credentials(study_id=submission_id)

        return InitiationResponse(
            submission_id=submission_id,
            ftp_credentials=FtpCredentials(
                host=details.ftp_host,
                username=details.ftp_user,
                password=details.ftp_password,
                upload_path=details.ftp_folder,
            ),
        )

    def _identify_study_category(self, context: SubmissionContext):
        assay_technologies = set()
        assay_types = set()
        for study in context.filtered_investigation.studies or []:
            for assay in study.assays:
                if "mass spectrometry" in assay.technologyType.annotationValue.lower():
                    assay_technologies.add("mass spectrometry")
                elif "nmr" in assay.technologyType.annotationValue.lower():
                    assay_technologies.add("nmr")
                assay_type_comments = [
                    x for x in assay.comments or [] if x.name == "assay type"
                ]
                if assay_type_comments and assay_type_comments[0].value:
                    assay_types.add(assay_type_comments[0].value.lower())

        if not assay_technologies:
            raise RepositorySubmissionError(
                "Study assay technology type is not defined."
            )

        if len(assay_technologies) > 1:
            raise RepositorySubmissionError(
                "Multiple assay technology type is not supported."
            )
        if "mass spectrometry" in assay_technologies:
            study_category = "ms-mhd-legacy"
            assay_type = "LC-MS"
        else:
            study_category = "nmr"
            assay_type = "nmr"
        return study_category, assay_type

    def _check_context(self, context: SubmissionContext):

        if not context or not context.repository_config:
            raise RepositoryConfigError("Token is required for MetaboLights submission")

        if (
            not context.filtered_investigation
            or not context.filtered_investigation.studies
        ):
            raise RepositoryConfigError(
                "Investigation or study is not defined for MetaboLIghts submission"
            )

    def upload_isa_json(
        self, context: SubmissionContext, repository_token: None | Token
    ) -> RepositoryResponse | None:
        self._check_context(context)

        if not repository_token or not repository_token.token:
            raise RepositorySubmissionError(
                "Token is required for MetaboLights Submission"
            )
        token = repository_token.token
        submission_url = context.repository_config.settings.get("submission-url")
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

        investigation_str = context.filtered_investigation.model_dump_json(
            by_alias=True, exclude_none=True
        )
        json_file = io.StringIO(investigation_str)

        files = {"isa_json_file": ("isa_json.json", json_file)}
        try:
            submission_response = requests.post(
                submission_url,
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
                    "Request towards MetaboLights failed!\n"
                    f"Request:\n"
                    f"Method:{submission_response.request.method}\n"
                    f"Status:{submission_response.status_code}\n"
                    f"URL:{submission_response.request.url}\n"
                    f"Headers:{submission_response.request.headers}\n"
                    f"Body:{response_body}"
                )

            submission_response.json()
        except Exception as exc:
            raise RepositorySubmissionError(self.repository_name, str(exc))

        # validation_url = self.find_value_in_info_section(
        #     "validation-url", result["info"]
        # )
        # validation_status_url = self.find_value_in_info_section(
        #     "validation-status-url", result["info"]
        # )
        # ftp_credentials_url = self.find_value_in_info_section(
        #     "ftp-credentials-url", result["info"]
        # )

        # transfer_config = context.data_file_transfer_config
        # if transfer_config and transfer_config.data_file_paths:
        #     protocol = transfer_config.transfer_protocol
        #     if protocol != "ftp":
        #         raise RepositorySubmissionError(
        #             f"Data upload protocol {protocol} is not supported"
        #         )

        #     ftp_credentials_response = requests.get(
        #         ftp_credentials_url, headers=headers
        #     )
        #     ftp_credentials_response.raise_for_status()
        #     ftp_credentials = ftp_credentials_response.json()
        #     uploader = FTPUploader(
        #         ftp_credentials["ftpHost"],
        #         ftp_credentials["ftpUser"],
        #         ftp_credentials["ftpPassword"],
        #     )
        #     uploader.upload(
        #         transfer_config.data_file_paths,
        #         target_location=ftp_credentials["ftpPath"],
        #     )

        # validation_response = requests.post(validation_url, headers=headers)
        # validation_response.raise_for_status()
        # pool_time_in_seconds = 10
        # max_pool_count = 100
        # validation_status_response = None
        # for _ in range(max_pool_count):
        #     timeout = False
        #     try:
        #         validation_status_response = requests.get(
        #             validation_status_url, headers=headers, timeout=30
        #         )
        #         validation_status_response.raise_for_status()
        #     except requests.exceptions.Timeout:
        #         timeout = True
        #     if not timeout:
        #         if validation_status_response is None:
        #             raise ValueError("Validation status response is None")
        #         validation_status = validation_status_response.json()
        #         validation_time = self.find_value_in_info_section(
        #             "validation-time", validation_status["info"], fail_gracefully=True
        #         )
        #         if validation_time:
        #             break
        #     time.sleep(pool_time_in_seconds)
        # else:
        #     raise ValueError(f"Validation failed after {max_pool_count} iterations")

        # if validation_status_response:
        #     return validation_status_response

        # raise ValueError("Submission failed for MetaboLights")

    def find_value_in_info_section(
        self, key: str, info_section: list[Any], fail_gracefully: bool = False
    ) -> Any:
        for info in info_section:
            if info["name"] == key:
                return info["message"]
        if fail_gracefully:
            return None
        raise ValueError(f"Name {key} not found in info section")
