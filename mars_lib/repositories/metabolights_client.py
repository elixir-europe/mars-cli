import io
import time
from urllib.parse import urlencode

import requests

import logging
from mars_lib.models.abstract_repository_client import (
    AuthError,
    BaseRepositoryClient,
    InitiationResponse,
    RepositoryClientError,
    RepositoryConfig,
    RepositoryConfigError,
    RepositoryInitiationError,
    SubmissionContext,
    RepositorySubmissionError,
    Token,
)
from mars_lib.models.isa_json import Investigation
from mars_lib.models.repository_response import RepositoryResponse

logger = logging.getLogger(__name__)


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
            if not settings.get("url"):
                errors.append(f"url is not defined for {label}")
            if not settings.get("auth-url"):
                errors.append(f"auth-url is not defined for {label}")
            if not settings.get("auth-client-name"):
                errors.append(f"auth-client-name is not defined for {label}")
            if not settings.get("initiation-url"):
                errors.append(f"initiation-url is not defined for {label}")
            if not settings.get("submission-url"):
                errors.append(f"submission-url is not defined for {label}")

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
        initiation_url = settings.get("initiation-url")
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {repository_token.token}",
        }
        files = None
        if submission_id:
            headers["submission-id"] = submission_id
        else:
            if not context.filtered_investigation:
                raise RepositorySubmissionError("Input ISA-JSON is not defined")
            investigation_str = context.filtered_investigation.model_dump_json(
                by_alias=True, exclude_none=True
            )
            json_file = io.StringIO(investigation_str)

            files = {"isa_json_file": ("isa_json.json", json_file)}
        response = requests.post(
            initiation_url,
            headers=headers,
            files=files,
            timeout=60,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as ex:
            response_content = response.content.decode("utf-8")
            raise AuthError(
                "ERROR when generating MetaboLights token. "
                f"Status: {response.status_code}. Response content:\n{response_content}"
            ) from ex
        response_dict = response.json()
        return InitiationResponse.model_validate(response_dict)

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
        submission_id = context.submission_id
        if not submission_id:
            raise RepositorySubmissionError("submission-id is required")

        token = repository_token.token
        submission_url = context.repository_config.settings.get("submission-url")
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {token}",
            "submission-id": submission_id,
        }

        investigation_str = context.filtered_investigation.model_dump_json(
            by_alias=True, exclude_none=True
        )
        json_file = io.StringIO(investigation_str)

        files = {"isa_json_file": ("isa_json.json", json_file)}
        response: None | RepositoryResponse = None
        try:
            submission_response = requests.post(
                submission_url,
                headers=headers,
                files=files,
                timeout=120,
            )
            submission_response.raise_for_status()
            response_dict = submission_response.json()
            response = RepositoryResponse.model_validate(response_dict)

        except Exception as exc:
            raise RepositorySubmissionError(self.repository_name, str(exc))
        result: None | RepositoryResponse = None
        try:
            headers["task-id"] = response.task_id
            submission_poll_url = context.repository_config.settings.get(
                "submission-url"
            )
            for _ in range(context.submission_max_retry):
                submission_response = requests.get(
                    submission_poll_url,
                    headers=headers,
                    timeout=10,
                )
                submission_response.raise_for_status()

                response = RepositoryResponse.model_validate(submission_response.json())
                status = response.submission_status
                if status != "PENDING":
                    result = response
                    break
                time.sleep(context.submission_retry_period_in_seconds)
            if not result:
                raise RepositorySubmissionError(
                    self.repository_name,
                    "Retries reach to maximum submission retry count",
                )
            return result
        except RepositoryClientError as ex:
            raise ex
        except Exception as exc:
            raise RepositorySubmissionError(self.repository_name, str(exc))
