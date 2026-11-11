import configparser

from mars_lib.settings import create_settings_file, generate_config


def test_create_settings_file_writes_logging_and_repository_defaults(tmp_path):
    create_settings_file(tmp_path)

    settings_path = tmp_path / "settings.ini"
    assert settings_path.is_file()

    config = configparser.ConfigParser()
    assert config.read(settings_path) == [str(settings_path)]
    assert config["logging"]["log_level"] == "ERROR"
    assert config["logging"]["log_file"] == str(tmp_path / "app.log")
    assert config["logging"]["log_max_size"] == "1024"
    assert config["logging"]["log_max_files"] == "5"
    assert config["metabolights"]["development-auth-client-name"] == "swagger-ui-test"
    assert config["metabolights"]["production-auth-client-name"] == "swagger-ui"
    assert config["ena"]["development-data-submission-url"] == "webin2.ebi.ac.uk"


def test_generate_config_creates_directory_and_settings_file(tmp_path):
    settings_dir = tmp_path / ".mars" / "nested"

    generate_config(settings_dir)

    assert settings_dir.is_dir()
    assert (settings_dir / "settings.ini").is_file()


def test_generate_config_does_not_overwrite_existing_file(tmp_path):
    settings_dir = tmp_path / ".mars"
    settings_dir.mkdir()
    existing_file = settings_dir / "settings.ini"
    existing_file.write_text("[custom]\nvalue = keep-me\n")

    generate_config(settings_dir)

    assert existing_file.read_text() == "[custom]\nvalue = keep-me\n"
