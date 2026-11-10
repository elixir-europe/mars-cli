import importlib
import sys
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
    # data_file_paths is the last positional argument of `submission`
    return mock_submission.call_args.args[-1]


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


def test_submit_without_data_folder_passes_empty_list(
    mars_cli, isa_json_file
):
    result, mock_submission = _invoke_submit(mars_cli, isa_json_file)

    assert result.exit_code == 0, result.output
    assert _passed_data_files(mock_submission) == []


def test_submit_rejects_nonexistent_data_folder(mars_cli, isa_json_file, tmp_path):
    result, mock_submission = _invoke_submit(
        mars_cli, isa_json_file, "--data-folder", str(tmp_path / "missing")
    )

    assert result.exit_code != 0
    mock_submission.assert_not_called()
