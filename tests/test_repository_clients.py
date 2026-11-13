from configparser import ConfigParser
import json
from urllib.parse import parse_qs

import pytest
import requests

from mars_lib.models.abstract_repository_client import (
    AuthError,
    BaseRepositoryClient,
    RepositoryConfig,
    RepositoryConfigError,
    RepositoryInitiationError,
    RepositorySubmissionError,
    SubmissionContext,
    Token,
)
from mars_lib.models.isa_json import Comment, Investigation
from mars_lib.repositories.metabolights_client import MetaboLightsRepositoryClient
from mars_lib.target_repo import TARGET_REPO_KEY


def _assay(index, target_repository="metabolights", technology="mass spectrometry"):
    return {
        "@id": f"#assay/{index}",
        "filename": f"a_assay_{index}.txt",
        "comments": [
            {"name": TARGET_REPO_KEY, "value": target_repository},
            {"name": "assay type", "value": "LC-MS"},
        ],
        "dataFiles": [],
        "materials": {"samples": [{"@id": "#sample/1"}]},
        "processSequence": [
            {
                "@id": f"#assay-process/{index}",
                "name": "measurement",
                "executesProtocol": {"@id": "#protocol/1"},
            }
        ],
        "technologyType": {"annotationValue": technology},
    }


def _study(index=1, assays=None):
    return {
        "@id": f"#study/{index}",
        "filename": f"s_study_{index}.txt",
        "title": f"Study {index}",
        "characteristicCategories": [
            {
                "@id": "#characteristic/organism",
                "characteristicType": {"annotationValue": "organism"},
            }
        ],
        "protocols": [{"@id": "#protocol/1", "name": "Extraction"}],
        "materials": {
            "sources": [
                {
                    "@id": "#source/1",
                    "name": "source 1",
                    "characteristics": [
                        {"category": {"@id": "#characteristic/organism"}}
                    ],
                }
            ],
            "samples": [
                {
                    "@id": "#sample/1",
                    "name": "sample 1",
                    "characteristics": [
                        {"category": {"@id": "#characteristic/organism"}}
                    ],
                    "derivesFrom": [{"@id": "#source/1"}],
                }
            ],
        },
        "processSequence": [
            {
                "@id": f"#study-process/{index}",
                "name": "extraction",
                "executesProtocol": {"@id": "#protocol/1"},
            }
        ],
        "assays": assays if assays is not None else [_assay(index)],
    }


def _investigation(studies=None):
    return Investigation.model_validate(
        {
            "@id": "#investigation/1",
            "filename": "i_investigation.txt",
            "title": "Test investigation",
            "studies": studies if studies is not None else [_study()],
        }
    )


def _repository_config(credentials=None, settings=None):
    return RepositoryConfig(
        credentials=credentials
        if credentials is not None
        else {"username": "test-user", "password": "test-password"},
        settings=settings
        if settings is not None
        else {
            "url": "https://metabolights.test/api",
            "auth-url": "https://metabolights.test/token",
            "auth-client-name": "mars-cli",
            "initiation-url": "https://metabolights.test/initiate",
            "submission-url": "https://metabolights.test/submit",
            "submission-poll-url": "https://metabolights.test/poll",
        },
    )


def _context(
    investigation=None,
    repository_config=None,
    submission_id=None,
    max_retry=3,
    retry_period=0,
):
    return SubmissionContext(
        is_development=True,
        filtered_investigation=investigation or _investigation(),
        repository_config=repository_config or _repository_config(),
        submission_id=submission_id,
        submission_max_retry=max_retry,
        submission_retry_period_in_seconds=retry_period,
    )


class _ConcreteRepositoryClient(BaseRepositoryClient):
    def check_repository_config(self, repository_config):
        return None

    def get_token(self, context):
        return None

    def upload_isa_json(self, context, repository_token):
        return None


class _Response:
    def __init__(self, payload=None, status_code=200, content=b"response body"):
        self.payload = payload or {}
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def test_reduce_investigation_filters_assays_and_protocols_without_mutating_input():
    investigation = _investigation(
        [
            _study(
                assays=[
                    _assay(1, target_repository="metabolights"),
                    _assay(2, target_repository="ena"),
                ]
            ),
            _study(
                index=2,
                assays=[_assay(3, target_repository="ena")],
            ),
        ]
    )
    client = _ConcreteRepositoryClient("metabolights", "metabolights")

    reduced = client.reduce_investigation(investigation)

    assert [study.id for study in reduced.studies] == ["#study/1"]
    assert [assay.id for assay in reduced.studies[0].assays] == ["#assay/1"]
    assert [protocol.id for protocol in reduced.studies[0].protocols] == ["#protocol/1"]
    assert len(investigation.studies) == 2
    assert len(investigation.studies[0].assays) == 2


def test_filter_comments_by_key_returns_only_matching_comments():
    client = _ConcreteRepositoryClient("metabolights", "metabolights")
    comments = [
        {"name": "other", "value": "keep out"},
        {"name": TARGET_REPO_KEY, "value": "ena"},
        {"name": TARGET_REPO_KEY, "value": "metabolights"},
    ]

    matches = client._filter_comments_by_key(
        [Comment.model_validate(comment) for comment in comments], TARGET_REPO_KEY
    )

    assert [comment.value for comment in matches] == ["ena", "metabolights"]
    assert client._filter_comments_by_key([], TARGET_REPO_KEY) == []


def test_get_repository_config_uses_development_settings_and_credentials():
    parser = ConfigParser()
    parser["metabolights"] = {
        "url": "https://production.test",
        "development-url": "https://development.test",
        "production-url": "https://production-override.test",
    }
    client = _ConcreteRepositoryClient("metabolights", "metabolights")

    config = client.get_repository_config(
        parser,
        credentials={"metabolights-development": {"username": "dev"}},
        is_development=True,
    )

    assert config.settings == {"url": "https://development.test"}
    assert config.credentials == {"username": "dev"}


def test_get_repository_config_rejects_missing_configuration():
    client = _ConcreteRepositoryClient("metabolights", "metabolights")

    with pytest.raises(RepositoryConfigError, match="configuration is not defined"):
        client.get_repository_config(credentials={})

    config = ConfigParser()
    config["other"] = {"setting": "value"}
    with pytest.raises(RepositoryConfigError, match="There is no metabolights section"):
        client.get_repository_config(config, credentials={})


def test_metabolights_config_validation_reports_missing_fields():
    client = MetaboLightsRepositoryClient()

    errors = client.check_repository_config(
        RepositoryConfig(
            credentials={"username": "test-user"}, settings={"other": "value"}
        )
    )

    assert errors == [
        "Password not defined for metabolights",
        "url is not defined for metabolights",
        "auth-url is not defined for metabolights",
        "auth-client-name is not defined for metabolights",
        "initiation-url is not defined for metabolights",
        "submission-url is not defined for metabolights",
    ]


def test_metabolights_config_validation_accepts_complete_configuration():
    client = MetaboLightsRepositoryClient()

    assert client.check_repository_config(_repository_config()) is None


def test_get_token_posts_password_grant_and_returns_both_tokens(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = _context()
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response({"access_token": "access", "refresh_token": "refresh"})

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post", fake_post
    )

    token = client.get_token(context)

    assert captured["url"] == "https://metabolights.test/token"
    assert captured["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(captured["data"]) == {
        "grant_type": ["password"],
        "scope": ["openid profile email"],
        "username": ["test-user"],
        "password": ["test-password"],
        "client_id": ["mars-cli"],
    }
    assert captured["timeout"] == 20
    assert token == Token(token="access", refresh_token="refresh")


def test_get_token_raises_auth_error_for_http_failure(monkeypatch):
    client = MetaboLightsRepositoryClient()

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post",
        lambda *args, **kwargs: _Response(status_code=401, content=b"bad credentials"),
    )

    with pytest.raises(AuthError, match="Status: 401"):
        client.get_token(_context())


def test_get_token_raises_auth_error_when_access_token_is_missing(monkeypatch):
    client = MetaboLightsRepositoryClient()
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post",
        lambda *args, **kwargs: _Response({"refresh_token": "refresh"}),
    )

    with pytest.raises(AuthError, match="ERROR when generating token"):
        client.get_token(_context())


def test_reduce_investigation_keeps_metabolights_assays_in_first_study():
    client = MetaboLightsRepositoryClient()
    investigation = _investigation(
        [
            _study(
                assays=[
                    _assay(1, target_repository="metabolights"),
                    _assay(2, target_repository="ena"),
                ]
            ),
            _study(index=2),
        ]
    )

    reduced = client.reduce_investigation(investigation)

    assert [study.id for study in reduced.studies] == ["#study/1"]
    assert [assay.id for assay in reduced.studies[0].assays] == ["#assay/1"]
    assert len(investigation.studies) == 2


def test_initiate_submission_posts_isa_json_and_parses_response(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = _context()
    captured = {}
    response = {
        "submissionId": "submission-1",
        "ftpCredentials": {
            "host": "ftp.test",
            "username": "ftp-user",
            "password": "ftp-password",
            "uploadPath": "/upload/1",
        },
    }

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(response)

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post", fake_post
    )

    result = client.initiate_submission(context, Token(token="access"))

    assert captured["url"] == "https://metabolights.test/initiate"
    assert captured["headers"] == {
        "accept": "application/json",
        "Authorization": "Bearer access",
    }
    assert captured["timeout"] == 60
    assert captured["files"]["isa_json_file"][0] == "isa_json.json"
    uploaded_isa = json.loads(captured["files"]["isa_json_file"][1].getvalue())
    assert uploaded_isa["@id"] == "#investigation/1"
    assert result.submission_id == "submission-1"
    assert result.ftp_credentials.upload_path == "/upload/1"


def test_initiate_submission_reuses_id_without_uploading_isa(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = _context(submission_id="existing-submission")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response({"submissionId": "existing-submission"})

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post", fake_post
    )

    result = client.initiate_submission(context, Token(token="access"))

    assert captured["headers"]["submission-id"] == "existing-submission"
    assert captured["files"] is None
    assert result.submission_id == "existing-submission"


def test_initiate_submission_requires_a_token():
    client = MetaboLightsRepositoryClient()

    with pytest.raises(RepositorySubmissionError, match="Token is required"):
        client.initiate_submission(_context(), None)


def test_initiate_submission_rejects_invalid_repository_configuration():
    client = MetaboLightsRepositoryClient()
    context = _context(repository_config=RepositoryConfig(credentials={}, settings={}))

    with pytest.raises(
        RepositoryInitiationError, match="repository configuration error"
    ):
        client.initiate_submission(context, Token(token="access"))


@pytest.mark.parametrize(
    ("technology", "expected"),
    [
        ("mass spectrometry", ("ms-mhd-legacy", "LC-MS")),
        ("NMR spectroscopy", ("nmr", "nmr")),
    ],
)
def test_identify_study_category_for_supported_technologies(technology, expected):
    client = MetaboLightsRepositoryClient()
    investigation = _investigation([_study(assays=[_assay(1, technology=technology)])])

    assert client._identify_study_category(_context(investigation)) == expected


def test_identify_study_category_rejects_mixed_technologies():
    client = MetaboLightsRepositoryClient()
    investigation = _investigation(
        [
            _study(
                assays=[
                    _assay(1, technology="mass spectrometry"),
                    _assay(2, technology="NMR spectroscopy"),
                ]
            )
        ]
    )

    with pytest.raises(RepositorySubmissionError, match="Multiple assay technology"):
        client._identify_study_category(_context(investigation))


@pytest.mark.skip("Will be updated later")
def test_upload_isa_json_puts_document_then_polls_until_complete(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = _context(submission_id="submission-1", max_retry=3, retry_period=0)
    captured = {}
    poll_responses = iter(
        [
            _Response(
                {
                    "targetRepository": "metabolights",
                    "submissionStatus": "PENDING",
                    "taskId": "task-1",
                }
            ),
            _Response(
                {
                    "targetRepository": "metabolights",
                    "submissionStatus": "SUCCESS",
                    "submissionId": "submission-1",
                    "taskId": "task-1",
                }
            ),
        ]
    )

    def fake_put(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _Response(
            {
                "targetRepository": "metabolights",
                "submissionStatus": "PENDING",
                "taskId": "task-1",
            }
        )

    get_calls = []

    def fake_get(url, **kwargs):
        get_calls.append((url, kwargs))
        return next(poll_responses)

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.put", fake_put
    )
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.get", fake_get
    )
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.time.sleep", lambda _: None
    )

    result = client.upload_isa_json(context, Token(token="access"))

    assert captured["url"] == "https://metabolights.test/submit"
    assert captured["headers"]["Authorization"] == "Bearer access"
    assert captured["headers"]["submission-id"] == "submission-1"
    assert captured["timeout"] == 120
    assert len(get_calls) == 2
    assert get_calls[-1][1]["headers"]["task-id"] == "task-1"
    assert result.submission_status == "SUCCESS"


def test_upload_isa_json_rejects_missing_submission_id():
    client = MetaboLightsRepositoryClient()

    with pytest.raises(RepositorySubmissionError, match="submission-id is required"):
        client.upload_isa_json(_context(), Token(token="access"))


@pytest.mark.skip("Update it later")
def test_upload_isa_json_reports_when_polling_never_finishes(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = _context(submission_id="submission-1", max_retry=2, retry_period=0)
    pending = {
        "targetRepository": "metabolights",
        "submissionStatus": "PENDING",
        "taskId": "task-1",
    }
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.put",
        lambda *args, **kwargs: _Response(pending),
    )
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.get",
        lambda *args, **kwargs: _Response(pending),
    )
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.time.sleep", lambda _: None
    )

    with pytest.raises(RepositorySubmissionError, match="Retries reach to maximum"):
        client.upload_isa_json(context, Token(token="access"))


@pytest.mark.skip("Update it later")
def test_upload_isa_json_wraps_put_errors(monkeypatch):
    client = MetaboLightsRepositoryClient()
    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.put",
        lambda *args, **kwargs: _Response(status_code=500),
    )

    with pytest.raises(RepositorySubmissionError, match="HTTP 500"):
        client.upload_isa_json(
            _context(submission_id="submission-1"), Token(token="access")
        )
