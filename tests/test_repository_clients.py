from configparser import ConfigParser
import json
from pathlib import Path
from types import SimpleNamespace
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
from mars_lib.models.isa_json import Investigation
from mars_lib.repositories.metabolights_client import MetaboLightsRepositoryClient


class _TestRepositoryClient(BaseRepositoryClient):
    def check_repository_config(self, repository_config):
        return None

    def get_token(self, context):
        return None

    def upload_isa_json(self, context, repository_token):
        return None


def _assay(target, technology="mass spectrometry", assay_type="LC-MS"):
    return {
        "comments": [
            {"name": "target_repository", "value": target},
            {"name": "assay type", "value": assay_type},
        ],
        "technologyType": {"annotationValue": technology},
    }


def _investigation(*studies):
    return Investigation.model_validate({"studies": list(studies)})


def _submission_context(*, submission_id=None, settings=None, investigation=None):
    return SubmissionContext(
        submission_id=submission_id,
        is_development=True,
        filtered_investigation=investigation
        or _investigation({"assays": [_assay("metabolights")]}),
        repository_config=RepositoryConfig(
            credentials={"username": "user", "password": "password"},
            settings={
                "auth-url": "https://auth.example.test/token",
                "auth-client-name": "mars",
                "submission-base-url": "https://submission.example.test",
                "auth-issuer": "https://issuer.example.test",
                "submission-url": "https://submission.example.test/isa",
                **(settings or {}),
            },
        ),
    )


def test_get_repository_config_selects_development_settings_and_credentials():
    client = _TestRepositoryClient("demo", "demo")
    config = ConfigParser()
    config.read_dict(
        {
            "demo": {
                "common-option": "shared",
                "development-url": "https://dev.example.test",
                "production-url": "https://prod.example.test",
            }
        }
    )

    result = client.get_repository_config(
        config=config,
        credentials={"demo-development": {"username": "dev-user"}},
        is_development=True,
    )

    assert result.settings == {
        "common-option": "shared",
        "url": "https://dev.example.test",
    }
    assert result.credentials == {"username": "dev-user"}


def test_get_repository_config_rejects_missing_configuration():
    client = _TestRepositoryClient("demo", "demo")

    with pytest.raises(RepositoryConfigError, match="configuration is not defined"):
        client.get_repository_config()
    with pytest.raises(RepositoryConfigError, match="There is no demo section"):
        client.get_repository_config(config=ConfigParser())


def test_reduce_investigation_keeps_target_assays_and_referenced_protocols():
    investigation = _investigation(
        {
            "assays": [_assay("metabolights"), _assay("ena")],
            "protocols": [{"@id": "used"}, {"@id": "unused"}],
            "processSequence": [
                {"executesProtocol": {"@id": "used"}},
            ],
        }
    )
    client = MetaboLightsRepositoryClient()

    reduced = client.reduce_investigation(investigation)

    assert len(reduced.studies) == 1
    assert len(reduced.studies[0].assays) == 1
    assert reduced.studies[0].assays[0].comments[0].value == "metabolights"
    assert [protocol.id for protocol in reduced.studies[0].protocols] == ["used"]
    # The source object is copied before filtering.
    assert len(investigation.studies[0].assays) == 2
    assert len(investigation.studies[0].protocols) == 2


def test_metabolights_reduce_investigation_keeps_only_first_study():
    investigation = _investigation(
        {"assays": [_assay("metabolights")]},
        {"assays": [_assay("metabolights")]},
    )

    reduced = MetaboLightsRepositoryClient().reduce_investigation(investigation)

    assert len(reduced.studies) == 1
    assert len(investigation.studies) == 2


@pytest.mark.parametrize(
    ("technology", "expected"),
    [
        ("mass spectrometry", ("ms-mhd-legacy", "LC-MS")),
        ("NMR spectroscopy", ("nmr", "nmr")),
    ],
)
def test_identify_study_category(technology, expected):
    investigation = _investigation(
        {"assays": [_assay("metabolights", technology=technology)]}
    )
    context = SubmissionContext(
        is_development=True,
        filtered_investigation=investigation,
        repository_config=RepositoryConfig(credentials={}, settings={}),
    )

    assert MetaboLightsRepositoryClient()._identify_study_category(context) == expected


def test_identify_study_category_rejects_missing_or_mixed_technology():
    client = MetaboLightsRepositoryClient()
    config = RepositoryConfig(credentials={}, settings={})

    for assays, message in [
        ([_assay("metabolights", technology="")], "technology type is not defined"),
        (
            [
                _assay("metabolights", technology="mass spectrometry"),
                _assay("metabolights", technology="NMR"),
            ],
            "Multiple assay technology type is not supported",
        ),
    ]:
        context = SubmissionContext(
            is_development=True,
            filtered_investigation=_investigation({"assays": assays}),
            repository_config=config,
        )
        with pytest.raises(RepositorySubmissionError, match=message):
            client._identify_study_category(context)


@pytest.mark.parametrize(
    ("repository_config", "expected_errors"),
    [
        (None, ["MetaboLights repository  configuration is not defined"]),
        (
            RepositoryConfig(credentials={}, settings={}),
            [
                "Credentials are not defined for metabolights",
                "Settings are not defined for metabolights",
            ],
        ),
        (
            RepositoryConfig(
                credentials={"username": "user"}, settings={"auth-url": "url"}
            ),
            [
                "Password not defined for metabolights",
                "auth-client-name is not defined for metabolights",
            ],
        ),
    ],
)
def test_check_repository_config(repository_config, expected_errors):
    assert (
        MetaboLightsRepositoryClient().check_repository_config(repository_config)
        == expected_errors
    )


def test_check_repository_config_accepts_complete_settings():
    assert (
        MetaboLightsRepositoryClient().check_repository_config(
            RepositoryConfig(
                credentials={"username": "user", "password": "password"},
                settings={
                    "auth-url": "https://auth.example.test",
                    "auth-client-name": "mars",
                },
            )
        )
        is None
    )


def test_get_token_posts_form_and_returns_tokens(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = SubmissionContext(
        is_development=True,
        filtered_investigation=_investigation({"assays": [_assay("metabolights")]}),
        repository_config=RepositoryConfig(
            credentials={"username": "user@example.test", "password": "secret"},
            settings={
                "auth-url": "https://auth.example.test/token",
                "auth-client-name": "mars",
            },
        ),
    )

    # Keep the response deliberately minimal to exercise the HTTP contract.
    class _Response:
        content = b""

        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "access", "refresh_token": "refresh"}

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post", fake_post
    )

    token = client.get_token(context)

    assert token == Token(token="access", refresh_token="refresh")
    assert calls[0][0] == "https://auth.example.test/token"
    assert calls[0][1]["timeout"] == 20
    assert parse_qs(calls[0][1]["data"]) == {
        "grant_type": ["password"],
        "scope": ["openid profile email"],
        "username": ["user@example.test"],
        "password": ["secret"],
        "client_id": ["mars"],
    }


def test_get_token_raises_auth_error_for_http_failure(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = SubmissionContext(
        is_development=True,
        filtered_investigation=_investigation({"assays": [_assay("metabolights")]}),
        repository_config=RepositoryConfig(
            credentials={"username": "user", "password": "bad"},
            settings={
                "auth-url": "https://auth.example.test",
                "auth-client-name": "mars",
            },
        ),
    )

    class _Response:
        status_code = 401
        content = b"invalid credentials"

        def raise_for_status(self):
            raise requests.HTTPError("unauthorized")

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post",
        lambda *args, **kwargs: _Response(),
    )

    with pytest.raises(AuthError):
        client.get_token(context)


def test_get_token_raises_auth_error_when_response_has_no_access_token(monkeypatch):
    context = _submission_context()

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"error": "invalid_grant"}

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post",
        lambda *args, **kwargs: _Response(),
    )

    with pytest.raises(AuthError, match="ERROR when generating token"):
        MetaboLightsRepositoryClient().get_token(context)


def test_find_value_in_info_section_missing_key():
    client = MetaboLightsRepositoryClient()
    info = [{"name": "validation-url", "message": "https://example.test/validate"}]

    assert (
        client.find_value_in_info_section("validation-url", info)
        == "https://example.test/validate"
    )
    assert (
        client.find_value_in_info_section("missing", info, fail_gracefully=True) is None
    )
    with pytest.raises(ValueError, match="Name missing not found"):
        client.find_value_in_info_section("missing", info)


def test_initiate_submission_reuses_existing_id_and_returns_ftp_credentials(
    monkeypatch,
):
    calls = []

    class _SubmissionClient:
        def __init__(self, base_url):
            calls.append(("init", base_url))

        def login_with_jwt(self, **kwargs):
            pytest.fail("Existing submissions should not log in or create a study")

        def get_private_ftp_credentials(self, study_id):
            calls.append(("ftp", study_id))
            return SimpleNamespace(
                ftp_host="ftp.example.test",
                ftp_user="ftp-user",
                ftp_password="ftp-password",
                ftp_folder="/incoming/MTBLS123",
            )

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.SubmissionClient", _SubmissionClient
    )
    context = _submission_context(submission_id="MTBLS123")

    result = MetaboLightsRepositoryClient().initiate_submission(
        context, Token(token="jwt-token", refresh_token="refresh")
    )

    assert result.submission_id == "MTBLS123"
    assert result.ftp_credentials.host == "ftp.example.test"
    assert result.ftp_credentials.username == "ftp-user"
    assert result.ftp_credentials.password == "ftp-password"
    assert result.ftp_credentials.upload_path == "/incoming/MTBLS123"
    assert calls == [
        ("init", "https://submission.example.test"),
        ("ftp", "MTBLS123"),
    ]


def test_initiate_submission_creates_study_and_cleans_temporary_request(monkeypatch):
    calls = []

    class _SubmissionClient:
        def __init__(self, base_url):
            calls.append(("init", base_url))

        def login_with_jwt(self, **kwargs):
            calls.append(("login", kwargs))

        def create_study(self, input_file_path):
            calls.append(("create", input_file_path))
            assert Path(input_file_path).exists()
            payload = json.loads(Path(input_file_path).read_text())
            assert payload["selectedStudyCategories"] == {"ms-mhd-legacy": ["LC-MS"]}
            assert payload["selectedSubmissionWorkflows"] == ["mars"]
            assert payload["datasetLicenseAgreement"] is True
            assert payload["datasetPolicyAgreement"] is True
            return {"studies": {"MTBLS_NEW": {}}}

        def get_private_ftp_credentials(self, study_id):
            calls.append(("ftp", study_id))
            return SimpleNamespace(
                ftp_host="ftp.example.test",
                ftp_user="new-user",
                ftp_password="new-password",
                ftp_folder="/incoming/MTBLS_NEW",
            )

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.SubmissionClient", _SubmissionClient
    )
    context = _submission_context()

    result = MetaboLightsRepositoryClient().initiate_submission(
        context, Token(token="jwt-token", refresh_token="refresh")
    )

    assert result.submission_id == "MTBLS_NEW"
    assert result.ftp_credentials.upload_path == "/incoming/MTBLS_NEW"
    login_args = calls[2][1]
    assert login_args == {
        "jwt_token": "jwt-token",
        "refresh_token": "refresh",
        "issuer": "https://issuer.example.test",
    }
    assert not Path(calls[3][1]).exists()


def test_initiate_submission_rejects_missing_token_and_invalid_context():
    client = MetaboLightsRepositoryClient()

    with pytest.raises(RepositorySubmissionError, match="Token is required"):
        client.initiate_submission(_submission_context(), None)
    with pytest.raises(
        RepositoryConfigError, match="Token is required for MetaboLights submission"
    ):
        client.initiate_submission(None, Token(token="jwt-token"))


def test_initiate_submission_rejects_incomplete_repository_config():
    context = _submission_context()
    context.repository_config = RepositoryConfig(credentials={}, settings={})

    with pytest.raises(
        RepositoryInitiationError, match="repository configuration error"
    ):
        MetaboLightsRepositoryClient().initiate_submission(
            context, Token(token="jwt-token")
        )


def test_initiate_submission_fails_when_study_creation_returns_no_study(monkeypatch):
    created_files = []

    class _SubmissionClient:
        def __init__(self, base_url):
            pass

        def login_with_jwt(self, **kwargs):
            pass

        def create_study(self, input_file_path):
            created_files.append(input_file_path)
            return {"studies": {}}

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.SubmissionClient", _SubmissionClient
    )

    with pytest.raises(
        RepositorySubmissionError, match="Submission id is not defined or created"
    ):
        MetaboLightsRepositoryClient().initiate_submission(
            _submission_context(), Token(token="jwt-token")
        )

    assert len(created_files) == 1
    assert not Path(created_files[0]).exists()


def test_upload_isa_json_posts_serialized_investigation(monkeypatch):
    context = _submission_context()
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"errors": []}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        filename, payload_file = kwargs["files"]["isa_json_file"]
        assert filename == "isa_json.json"
        assert json.loads(
            payload_file.getvalue()
        ) == context.filtered_investigation.model_dump(by_alias=True, exclude_none=True)
        return _Response()

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post", fake_post
    )

    result = MetaboLightsRepositoryClient().upload_isa_json(
        context, Token(token="jwt-token")
    )

    assert result is None
    assert calls[0][0] == "https://submission.example.test/isa"
    assert calls[0][1]["headers"] == {
        "accept": "application/json",
        "Authorization": "Bearer jwt-token",
    }
    assert calls[0][1]["timeout"] == 120


def test_upload_isa_json_wraps_repository_validation_errors(monkeypatch):
    context = _submission_context()

    class _Response:
        status_code = 400
        request = SimpleNamespace(
            body=b"request body",
            method="POST",
            url="https://submission.example.test/isa",
            headers={"Authorization": "Bearer jwt-token"},
        )

        def raise_for_status(self):
            return None

        def json(self):
            return {"errors": [{"message": "invalid ISA"}]}

    monkeypatch.setattr(
        "mars_lib.repositories.metabolights_client.requests.post",
        lambda *args, **kwargs: _Response(),
    )

    with pytest.raises(
        RepositorySubmissionError, match="Request towards MetaboLights failed"
    ):
        MetaboLightsRepositoryClient().upload_isa_json(
            context, Token(token="jwt-token")
        )


def test_upload_isa_json_requires_submission_token():
    with pytest.raises(RepositorySubmissionError, match="Token is required"):
        MetaboLightsRepositoryClient().upload_isa_json(_submission_context(), None)
