from configparser import ConfigParser
from urllib.parse import parse_qs

import pytest
import requests

from mars_lib.models.abstract_repository_client import (
    AuthError,
    BaseRepositoryClient,
    RepositoryConfig,
    RepositoryConfigError,
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
        (RepositoryConfig(credentials={}, settings={}), [
            "Credentials are not defined for metabolights",
            "Settings are not defined for metabolights",
        ]),
        (
            RepositoryConfig(credentials={"username": "user"}, settings={"auth-url": "url"}),
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


def test_get_token_posts_form_and_returns_tokens(monkeypatch):
    client = MetaboLightsRepositoryClient()
    context = SubmissionContext(
        is_development=True,
        filtered_investigation=_investigation({"assays": [_assay("metabolights")]}),
        repository_config=RepositoryConfig(
            credentials={"username": "user@example.test", "password": "secret"},
            settings={"auth-url": "https://auth.example.test/token", "auth-client-name": "mars"},
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

    monkeypatch.setattr("mars_lib.repositories.metabolights_client.requests.post", fake_post)

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
            settings={"auth-url": "https://auth.example.test", "auth-client-name": "mars"},
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

    with pytest.raises(AuthError, match="Status: 401.*invalid credentials"):
        client.get_token(context)


def test_find_value_in_info_section_missing_key():
    client = MetaboLightsRepositoryClient()
    info = [{"name": "validation-url", "message": "https://example.test/validate"}]

    assert client.find_value_in_info_section("validation-url", info) == "https://example.test/validate"
    assert client.find_value_in_info_section("missing", info, fail_gracefully=True) is None
    with pytest.raises(ValueError, match="Name missing not found"):
        client.find_value_in_info_section("missing", info)
