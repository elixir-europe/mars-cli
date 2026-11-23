import configparser
import pathlib


def create_settings_file(settings_dir):
    """
    Create a settings file with the specified log path and settings path.

    Args:
        settings_path (str): The path to the settings file.

    Returns:
        None
    """
    log_path = settings_dir / "app.log"
    settings_path = settings_dir / "settings.ini"
    config = configparser.ConfigParser()
    config["logging"] = {
        "log_level": "ERROR",
        "log_file": log_path,
        "log_max_size": "1024",
        "log_max_files": "5",
    }

    config["webin"] = {
        "development-url": "https://wwwdev.ebi.ac.uk/ena/dev/submit/webin/auth",
        "development-token-url": "https://wwwdev.ebi.ac.uk/ena/dev/submit/webin/auth/token",
        "production-url": "https://www.ebi.ac.uk/ena/submit/webin/auth",
        "production-token-url": "https://www.ebi.ac.uk/ena/submit/webin/auth/token",
    }

    config["ena"] = {
        "development-url": "https://wwwdev.ebi.ac.uk/ena/submit/webin-v2/",
        "development-submission-url": "https://wwwdev.ebi.ac.uk/ena/submit/drop-box/submit/?auth=ENA",
        "development-data-submission-url": "webin2.ebi.ac.uk",
        "production-url": "https://www.ebi.ac.uk/ena/submit/webin-v2/",
        "production-submission-url": "https://www.ebi.ac.uk/ena/submit/drop-box/submit/?auth=ENA",
        "production-data-submission-url": "webin2.ebi.ac.uk",
    }

    config["biosamples"] = {
        "development-url": "https://wwwdev.ebi.ac.uk/biosamples/samples/",
        "development-submission-url": "https://wwwdev.ebi.ac.uk/biosamples/samples/",
        "production-url": "https://www.ebi.ac.uk/biosamples/samples/",
        "production-submission-url": "https://www.ebi.ac.uk/biosamples/samples/",
    }
    config["metabolights"] = {
        "production-url": "https://www.ebi.ac.uk/metabolights/ws3/version",
        "production-submission-url": "https://www.ebi.ac.uk/metabolights/ws/isa-json/submit",
        "production-initiation-url": "https://www.ebi.ac.uk/metabolights/ws/isa-json/initiate",
        "production-auth-url": "https://www.ebi.ac.uk/metabolights/iam/realms/metabolights/protocol/openid-connect/token",
        "production-auth-client-name": "swagger-ui",
        "development-url": "https://wwwdev.ebi.ac.uk/metabolights/test/ws3/version",
        "development-submission-url": "https://wwwdev.ebi.ac.uk/metabolights/test/ws/isa-json/submit",
        "development-initiation-url": "https://wwwdev.ebi.ac.uk/metabolights/test/ws/isa-json/initiate",
        "development-auth-url": "https://wwwdev.ebi.ac.uk/metabolights/test/iam/realms/metabolights/protocol/openid-connect/token",
        "development-auth-client-name": "swagger-ui-test",
    }
    with open(settings_path, "w") as config_file:
        config.write(config_file)


def generate_config(settings_dir: pathlib.Path) -> None:
    """
    Generate the configuration file for the MARS CLI, unless it already exists.

    Args:
        settings_dir (pathlib.Path): The '.mars' directory to store the settings file in.

    Returns:
        None
    """
    settings_dir.mkdir(parents=True, exist_ok=True)

    if (settings_dir / "settings.ini").exists():
        return

    create_settings_file(settings_dir)
