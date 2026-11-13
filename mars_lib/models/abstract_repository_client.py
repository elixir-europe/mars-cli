import abc
from configparser import ConfigParser
import datetime
import enum
import pathlib
from typing import Annotated

from pydantic import Field, BaseModel, ConfigDict

from mars_lib.ftp_upload import FTPUploader
from mars_lib.models.isa_json import Comment, Investigation
from mars_lib.models.repository_response import RepositoryResponse
from mars_lib.target_repo import TARGET_REPO_KEY
from pydantic.alias_generators import to_camel


class RepositoryClientError(Exception): ...


class AuthError(RepositoryClientError): ...


class RepositoryConfigError(RepositoryClientError): ...


class RepositoryInitiationError(RepositoryClientError): ...


class RepositoryDataUploadError(RepositoryClientError): ...


class RepositorySubmissionError(RepositoryClientError): ...


class TooEarlyError(RepositoryClientError): ...


SUBMISSION_ID_KEY = "submission_id"


class DataTransferProtocol(enum.StrEnum):
    FTP = "ftp"
    ASPERA = "aspera"


class AbstractClientModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Token(AbstractClientModel):
    token: Annotated[
        None | str, Field(description="Token associated with this submission.")
    ] = None
    refresh_token: Annotated[
        None | str, Field(description="Refresh token associated with this submission.")
    ] = None


class ExecutionHistory(AbstractClientModel):
    repository: Annotated[
        str, Field(description="Repository associated with this submission.")
    ]
    submission_id: Annotated[
        str, Field(description="Submission id associated with this submission.")
    ]
    execution_time: Annotated[
        datetime.datetime,
        Field(description="Execution time associated with this submission."),
    ]
    input_investigation: Annotated[
        None | Investigation,
        Field(description="Input investigation associated with this submission."),
    ] = None
    output_response: Annotated[
        None | RepositoryResponse,
        Field(description="Output response associated with this submission."),
    ] = None


class RepositoryConfig(AbstractClientModel):
    credentials: Annotated[
        None | dict[str, None | str],
        Field(description="Credentials associated with this submission."),
    ] = None
    settings: Annotated[
        None | dict[str, None | str],
        Field(description="Settings associated with this submission."),
    ] = None


class FtpCredentials(AbstractClientModel):
    host: Annotated[
        None | str, Field(description="Ftp server associated with this submission.")
    ]
    username: Annotated[
        None | str, Field(description="Username associated with this submission.")
    ]
    password: Annotated[
        None | str, Field(description="Password associated with this submission.")
    ]
    upload_path: Annotated[
        None | str, Field(description="Upload path associated with this submission.")
    ]


class InitiationResponse(AbstractClientModel):
    submission_id: Annotated[
        None | str, Field(description="Submission id associated with this submission.")
    ] = None
    ftp_credentials: Annotated[
        None | FtpCredentials,
        Field(description="Ftp credentials associated with this submission."),
    ] = None
    assay_comments: Annotated[
        None | dict[str, str],
        Field(description="Additional assay comments associated with this submission."),
    ] = None
    errors: None | list[str] = None


class DataFileTransferConfig(AbstractClientModel):
    data_root_folder: Annotated[
        None | str,
        Field(description="Data root path associated with this submission."),
    ] = None
    data_file_paths: Annotated[
        None | list[str],
        Field(description="Data file paths associated with this submission."),
    ] = None
    transfer_protocol: Annotated[
        DataTransferProtocol,
        Field(description="Transfer protocol associated with this submission."),
    ] = DataTransferProtocol.FTP
    credentials: Annotated[
        None | FtpCredentials,
        Field(description="Credentials associated with this submission."),
    ] = None


class SubmissionContext(AbstractClientModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    is_development: Annotated[
        bool, Field(description="Is development associated with this submission.")
    ]
    filtered_investigation: Annotated[
        Investigation,
        Field(description="Filtered investigation associated with this submission."),
    ]
    repository_config: Annotated[
        RepositoryConfig,
        Field(description="Repository config associated with this submission."),
    ]

    submission_id: Annotated[
        None | str, Field(description="Submission id associated with this submission.")
    ] = None
    data_file_transfer_config: Annotated[
        None | DataFileTransferConfig,
        Field(description="Data file transfer config associated with this submission."),
    ] = None
    history: Annotated[
        None | list[ExecutionHistory],
        Field(description="History associated with this submission."),
    ] = None
    submission_max_retry: Annotated[
        int, Field(description="Submission max retry associated with this submission.")
    ] = 10
    submission_retry_period_in_seconds: Annotated[
        int,
        Field(
            description="Submission retry period in seconds associated with this submission."
        ),
    ] = 10


class BaseRepositoryClient(abc.ABC):
    def __init__(self, repository_name: str, repository_config_key: str, **kwargs):
        self.repository_name = repository_name
        self.repository_config_key = repository_config_key
        self.kwargs = kwargs

    @abc.abstractmethod
    def check_repository_config(
        self,
        repository_config: RepositoryConfig,
    ) -> list[str]: ...

    @abc.abstractmethod
    def get_token(self, context: SubmissionContext) -> None | Token: ...

    @abc.abstractmethod
    def upload_isa_json(
        self, context: SubmissionContext, repository_token: None | Token
    ) -> RepositoryResponse: ...

    def upload_data_files(self, context: SubmissionContext) -> None:
        if (
            not context.data_file_transfer_config
            or not context.data_file_transfer_config.credentials
        ):
            raise RepositoryDataUploadError(
                "metabolights",
                context.submission_id,
                "FTP credentials are not defined.",
            )

        credentials = context.data_file_transfer_config.credentials
        data_file_paths = [
            pathlib.Path(x)
            for x in context.data_file_transfer_config.data_file_paths or []
        ]
        uploader = FTPUploader(
            credentials.host, credentials.username, credentials.password
        )
        uploader.upload(data_file_paths, target_location=credentials.upload_path)

    def reduce_investigation(self, input_investigation: Investigation) -> Investigation:
        """
        Filters out assays that are not meant to be sent to target repository.

        Args:
            input_investigation (Investigation): Input ISA JSON that contains the original information.

        Returns:
            Investigation: Filtered ISA JSON.
        """
        filtered_investigation = input_investigation.model_copy(deep=True)
        filtered_investigation = self._filter_investigation_by_assay(
            filtered_investigation
        )
        referenced_protocols = set()
        for study in filtered_investigation.studies or []:
            for seq in study.processSequence or []:
                if seq.executesProtocol and seq.executesProtocol.id:
                    referenced_protocols.add(seq.executesProtocol.id)
            for assay in study.assays or []:
                for seq in assay.processSequence or []:
                    if seq.executesProtocol and seq.executesProtocol.id:
                        referenced_protocols.add(seq.executesProtocol.id)
        for study in filtered_investigation.studies or []:
            study.protocols = [
                x for x in study.protocols if x.id in referenced_protocols
            ]
        return filtered_investigation

    def get_repository_config(
        self,
        config: None | ConfigParser = None,
        credentials: None | dict = None,
        is_development: bool = False,
    ) -> RepositoryConfig:
        repository_settings = {}
        key = self.repository_config_key
        if not config:
            raise RepositoryConfigError("configuration is not defined")

        if config and key not in config.sections():
            raise RepositoryConfigError(
                f"There is no {self.repository_config_key} section in configuration"
            )
        if config:
            for item in config[key]:
                val = config[key].get(item)
                if is_development and item.startswith("development-"):
                    new_key = item.replace("development-", "", 1)
                    repository_settings[new_key] = val
                elif not is_development and item.startswith("production-"):
                    new_key = item.replace("production-", "", 1)
                    repository_settings[new_key] = val
                elif not item.startswith(("development-", "production-")):
                    repository_settings[item] = val

        if is_development:
            credentials_key = f"{key}-development"
            repository_credentials = credentials.get(credentials_key)
        else:
            repository_credentials = credentials.get(credentials_key) or {}

        return RepositoryConfig(
            credentials=repository_credentials, settings=repository_settings
        )

    ###########################################################################################
    # PRIVATE METHODS
    ###########################################################################################

    def _filter_investigation_by_assay(
        self, investigation: Investigation
    ) -> Investigation:
        new_studies = []
        studies = investigation.studies
        for study in studies:
            filtered_assays = []
            for assay in study.assays:
                repository_comments = self._filter_comments_by_key(
                    assay.comments, TARGET_REPO_KEY
                )

                comment_value = (
                    repository_comments[0].value if repository_comments else None
                )

                if comment_value and comment_value == self.repository_name:
                    filtered_assays.append(assay)

            if filtered_assays:
                study.assays = filtered_assays
                new_studies.append(study)

        investigation.studies = new_studies
        return investigation

    def _filter_comments_by_key(
        self, comments: list[Comment], key: str
    ) -> list[Comment]:
        filtered_comments = [x for x in comments or [] if x.name == key]

        return filtered_comments or []
