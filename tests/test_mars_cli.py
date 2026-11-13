import importlib
import logging
import sys
from configparser import ConfigParser
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


@pytest.fixture
def mars_cli(tmp_path, monkeypatch):
    """Import mars_cli with its settings directory redirected to a temp dir,
    so that importing it never touches the real ~/.mars folder."""
    monkeypatch.setenv("MARS_SETTINGS_DIR", str(tmp_path / "settings"))
    sys.modules.pop("mars_cli", None)
    module = importlib.import_module("mars_cli")
    yield module
    sys.modules.pop("mars_cli", None)


@pytest.fixture
def isa_json_file(tmp_path) -> Path:
    path = tmp_path / "isa.json"
    path.write_text("{}")
    return path


def _invoke_submit(mars_cli, isa_json_file, *extra_args):
    mock_submission = MagicMock()
    mars_cli.submission = mock_submission
    result = CliRunner().invoke(
        mars_cli.cli,
        [
            "--development",
            "submit",
            "--submit-to-metabolights",
            "False",
            *extra_args,
            str(isa_json_file),
        ],
    )
    return result, mock_submission


def _passed_data_files(mock_submission) -> list[str]:
    return mock_submission.call_args.kwargs["data_file_paths"]


def test_submit_lists_all_files_in_data_folder(mars_cli, isa_json_file, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "b.fastq.gz").write_text("b")
    (data / "a.fastq.gz").write_text("a")

    result, mock_submission = _invoke_submit(
        mars_cli, isa_json_file, "--data-folder", str(data)
    )

    assert result.exit_code == 0, result.output
    assert _passed_data_files(mock_submission) == [
        str(data / "a.fastq.gz"),
        str(data / "b.fastq.gz"),
    ]


def test_submit_walks_subfolders_recursively(mars_cli, isa_json_file, tmp_path):
    data = tmp_path / "data"
    (data / "nested" / "deeper").mkdir(parents=True)
    (data / "top.txt").write_text("")
    (data / "nested" / "mid.txt").write_text("")
    (data / "nested" / "deeper" / "low.txt").write_text("")

    result, mock_submission = _invoke_submit(
        mars_cli, isa_json_file, "--data-folder", str(data)
    )

    assert result.exit_code == 0, result.output
    assert sorted(_passed_data_files(mock_submission)) == sorted(
        [
            str(data / "top.txt"),
            str(data / "nested" / "mid.txt"),
            str(data / "nested" / "deeper" / "low.txt"),
        ]
    )


def test_submit_combines_multiple_data_folders(mars_cli, isa_json_file, tmp_path):
    folder_one = tmp_path / "one"
    folder_two = tmp_path / "two"
    folder_one.mkdir()
    folder_two.mkdir()
    (folder_one / "x.txt").write_text("")
    (folder_two / "y.txt").write_text("")

    result, mock_submission = _invoke_submit(
        mars_cli,
        isa_json_file,
        "--data-folder",
        str(folder_one),
        "--data-folder",
        str(folder_two),
    )

    assert result.exit_code == 0, result.output
    assert sorted(_passed_data_files(mock_submission)) == [
        str(folder_one / "x.txt"),
        str(folder_two / "y.txt"),
    ]


def test_submit_without_data_folder_passes_empty_list(mars_cli, isa_json_file):
    result, mock_submission = _invoke_submit(mars_cli, isa_json_file)

    assert result.exit_code == 0, result.output
    assert _passed_data_files(mock_submission) == []


def test_submit_rejects_nonexistent_data_folder(mars_cli, isa_json_file, tmp_path):
    result, mock_submission = _invoke_submit(
        mars_cli, isa_json_file, "--data-folder", str(tmp_path / "missing")
    )

    assert result.exit_code != 0
    mock_submission.assert_not_called()


TEST_ISA_JSON = Path("MARS/test-data/ena-complete.json")


def _isa_data_file_names() -> list[str]:
    from mars_lib.isa_json import load_investigation

    investigation = load_investigation(str(TEST_ISA_JSON))
    return sorted(
        {
            df.name
            for study in investigation.studies
            for assay in study.assays
            for df in assay.dataFiles
        }
    )


def _invoke_check(mars_cli, *extra_args):
    return CliRunner().invoke(
        mars_cli.cli, ["check-data-files", str(TEST_ISA_JSON), *extra_args]
    )


def _records(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_check_data_files_reports_counts(mars_cli, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="MARS-CLI")
    names = _isa_data_file_names()
    data = tmp_path / "data"
    data.mkdir()
    for name in names[1:]:
        (data / name).write_text("")
    (data / "not-in-isa.fastq").write_text("")

    result = _invoke_check(mars_cli, "--data-folder", str(data))

    assert result.exit_code == 1, result.output
    assert f"matched to a data file in the ISA JSON: {len(names) - 1}" in result.stdout
    assert "not referenced in the ISA JSON: 1" in result.stdout
    assert "without a matching data file: 1" in result.stdout
    assert (
        f"Not referenced in the ISA JSON: {data / 'not-in-isa.fastq'}" in result.stdout
    )
    assert f"Missing data file: {names[0]}" in result.stdout
    # The report goes to stdout only, not to the log
    assert "matched to a data file" not in "\n".join(
        r.getMessage() for r in caplog.records
    )
    assert _records(caplog, logging.WARNING) == []
    assert _records(caplog, logging.ERROR) == []


def test_check_data_files_succeeds_when_everything_matches(mars_cli, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="MARS-CLI")
    names = _isa_data_file_names()
    data = tmp_path / "data"
    data.mkdir()
    for name in names:
        (data / name).write_text("")

    result = _invoke_check(mars_cli, "--data-folder", str(data))

    assert result.exit_code == 0, result.output
    assert f"matched to a data file in the ISA JSON: {len(names)}" in result.stdout
    assert "not referenced in the ISA JSON: 0" in result.stdout
    assert "without a matching data file: 0" in result.stdout
    assert _records(caplog, logging.WARNING) == []
    assert _records(caplog, logging.ERROR) == []


def test_check_data_files_rejects_a_file_as_data_folder(mars_cli, tmp_path):
    a_file = tmp_path / "reads.fastq"
    a_file.write_text("")

    result = _invoke_check(mars_cli, "--data-folder", str(a_file))

    assert result.exit_code == 2
    assert "is a file" in result.stderr


def test_submit_rejects_a_file_as_data_folder(mars_cli, isa_json_file, tmp_path):
    a_file = tmp_path / "reads.fastq"
    a_file.write_text("")

    result, mock_submission = _invoke_submit(
        mars_cli, isa_json_file, "--data-folder", str(a_file)
    )

    assert result.exit_code == 2
    assert "is a file" in result.stderr
    mock_submission.assert_not_called()


def test_check_data_files_reports_invalid_isa_json_without_traceback(
    mars_cli, tmp_path, caplog
):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"unexpected": 1}')

    result = CliRunner().invoke(mars_cli.cli, ["check-data-files", str(invalid)])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert any(
        "Validation errors occurred" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.ERROR
    )


def test_submission_fails_before_submitting_when_data_files_are_missing(
    mars_cli, tmp_path, monkeypatch
):
    from mars_lib import submit as submit_module

    # Must never reach a target repository
    for name in ["submit_to_biosamples", "upload_to_ena", "submit_to_ena"]:
        monkeypatch.setattr(
            submit_module,
            name,
            MagicMock(side_effect=AssertionError("should not be called")),
        )
    with open("tests/test_credentials_example.json") as credentials_file:
        with pytest.raises(ValueError, match="missing from the data files"):
            submit_module.submission(
                webin_username="",
                metabolights_username="",
                credentials_file=credentials_file,
                isa_json_file=str(TEST_ISA_JSON),
                target_repositories=["biosamples", "ena"],
                urls={"BIOSAMPLES": {"SUBMISSION": ""}, "WEBIN": {"TOKEN": ""}},
                file_transfer="ftp",
                output=str(tmp_path / "out"),
                config=ConfigParser(),
                data_file_paths=[],
            )


METABOLIGHTS_DATA_FILES = [
    "metpro-analysis.txt",
    "ms-data-metpro--1.mzml",
    "ms-data-metpro--2.mzml",
    "ms-data-metpro--3.mzml",
    "ms-data-metpro--4.mzml",
]


def test_submit_with_ena_disabled_only_maps_data_files_of_submitted_assays(
    mars_cli, tmp_path, monkeypatch
):
    """Issue #51: with '--submit-to-ena False', the data files of the ENA assay must
    not be mapped to the files in '--data-folder', so leaving them out is fine."""
    from mars_lib import submit as submit_module
    from mars_lib.isa_json import load_investigation, map_data_files_to_repositories
    from mars_lib.models.abstract_repository_client import RepositoryConfig, Token
    from mars_lib.repositories.metabolights_client import MetaboLightsRepositoryClient

    # One investigation with a MetaboLights assay and an ENA assay
    investigation = load_investigation("MARS/test-data/metabolights-lcms.json")
    ena_assay = load_investigation(str(TEST_ISA_JSON)).studies[0].assays[0]
    investigation.studies[0].assays.append(ena_assay)
    isa_json = tmp_path / "isa.json"
    isa_json.write_text(investigation.model_dump_json(by_alias=True, exclude_none=True))

    # The data folder only holds the data files of the MetaboLights assay
    data = tmp_path / "data"
    data.mkdir()
    for name in METABOLIGHTS_DATA_FILES:
        (data / name).write_text(name)

    # Stop at the MetaboLights client without making repository requests
    class ReachedMetaboLights(Exception):
        pass

    repository_client = MagicMock(spec=MetaboLightsRepositoryClient)
    repository_client.repository_name = "metabolights"
    repository_client.reduce_investigation.return_value = investigation
    repository_client.get_repository_config.return_value = RepositoryConfig(
        credentials={"username": "test", "password": "test"}, settings={}
    )
    repository_client.get_token.return_value = Token(token="test-token")
    repository_client.initiate_submission.return_value = None
    repository_client.upload_isa_json.side_effect = ReachedMetaboLights
    client_factory = MagicMock(return_value=repository_client)
    monkeypatch.setattr(submit_module, "MetaboLightsRepositoryClient", client_factory)

    original_map_data_files = map_data_files_to_repositories
    mapped_files = {}

    def capture_data_file_mapping(*, files, investigation, target_repositories):
        result = original_map_data_files(
            files=files,
            investigation=investigation,
            target_repositories=target_repositories,
        )
        mapped_files.update(result)
        return result

    monkeypatch.setattr(
        submit_module, "map_data_files_to_repositories", capture_data_file_mapping
    )

    result = CliRunner().invoke(
        mars_cli.cli,
        [
            "--development",
            "submit",
            "--credentials-file",
            "tests/test_credentials_example.json",
            "--submit-to-biosamples",
            "False",
            "--submit-to-ena",
            "False",
            "--data-folder",
            str(data),
            str(isa_json),
        ],
    )

    assert isinstance(result.exception, ReachedMetaboLights), result.output
    client_factory.assert_called_once_with()
    repository_client.upload_isa_json.assert_called_once()
    assert mapped_files == {
        "metabolights": [str(data / name) for name in METABOLIGHTS_DATA_FILES]
    }


@pytest.mark.skip(reason="Data files are not mapped.")
def test_submission_ignores_data_files_of_assays_not_submitted(
    mars_cli, tmp_path, monkeypatch
):
    from mars_lib import submit as submit_module

    class ReachedBioSamples(Exception):
        pass

    # The ENA assay is not submitted, so its missing data files must not stop the
    # submission before it reaches BioSamples
    monkeypatch.setattr(
        submit_module, "submit_to_biosamples", MagicMock(side_effect=ReachedBioSamples)
    )
    with open("tests/test_credentials_example.json") as credentials_file:
        with pytest.raises(ReachedBioSamples):
            submit_module.submission(
                webin_username="",
                metabolights_username="",
                credentials_file=credentials_file,
                isa_json_file=str(TEST_ISA_JSON),
                target_repositories=["biosamples", "ena"],
                urls={},
                file_transfer="ftp",
                output=str(tmp_path / "out"),
                config=ConfigParser(),
                data_file_paths=[],
            )


METABOLIGHTS_DATA_FILES = [
    "metpro-analysis.txt",
    "ms-data-metpro--1.mzml",
    "ms-data-metpro--2.mzml",
    "ms-data-metpro--3.mzml",
    "ms-data-metpro--4.mzml",
]


def test_submission_fails_before_submitting_when_a_repository_has_no_assays(
    mars_cli, tmp_path, monkeypatch
):
    from mars_lib import submit as submit_module

    # The ISA JSON only has an ENA assay, so there is nothing to submit to
    # MetaboLights: BioSamples must not be reached either
    monkeypatch.setattr(
        submit_module,
        "submit_to_biosamples",
        MagicMock(side_effect=AssertionError("should not be called")),
    )
    data = tmp_path / "data"
    data.mkdir()
    for name in _isa_data_file_names():
        (data / name).write_text("")

    with open("tests/test_credentials_example.json") as credentials_file:
        with pytest.raises(ValueError, match="no assays for the target repository"):
            submit_module.submission(
                webin_username="",
                metabolights_username="",
                credentials_file=credentials_file,
                isa_json_file=str(TEST_ISA_JSON),
                target_repositories=["biosamples", "metabolights"],
                urls={},
                file_transfer="ftp",
                output=str(tmp_path / "out"),
                data_file_paths=sorted(str(f) for f in data.iterdir()),
                config=ConfigParser(),
            )
