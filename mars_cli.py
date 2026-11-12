import os
import sys
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path

import click
from pydantic_core import ValidationError
import requests

from mars_lib.credential import CredentialManager
from mars_lib.isa_json import check_data_files, load_investigation
from mars_lib.logging import init_logging, print_and_log
from mars_lib.settings import generate_config
from mars_lib.submit import submission
from mars_lib.target_repo import TargetRepository

# Load CLI configuration
home_dir = (
    Path(str(os.getenv("MARS_SETTINGS_DIR")))
    if os.getenv("MARS_SETTINGS_DIR")
    else Path.home()
)

config_file = home_dir / ".mars" / "settings.ini"
fallback_log_file = home_dir / ".mars" / "app.log"

# Create a default settings file on first run
generate_config(home_dir / ".mars")

config = ConfigParser()
config.read(config_file)

# Load logging configuration
init_logging(config, fallback_log_file)

# Read in all the URLs from the config file
urls = TargetRepository.get_repository_urls_from_config(config)


def list_files_in_folders(folders) -> list[str]:
    """Recursively list every file in the provided folders."""
    return [
        os.path.join(root, file_name)
        for folder in folders
        for root, _dirs, file_names in os.walk(folder)
        for file_name in sorted(file_names)
    ]


@click.group()
@click.option(
    "--development",
    "-d",
    is_flag=True,
    help="Boolean indicating the usage of the development environment of the target repositories. If not present, the production instances will be used.",
)
@click.pass_context
def cli(ctx, development):
    print_and_log("############# Welcome to the MARS CLI. #############")
    print_and_log(
        "Sensitive information might be dumped in the log files when setting the 'log_level' to DEBUG in the config file. Logging in debug mode should only be used for developing purpose a can implicate security issues if used in a production environment!",
        "debug",
    )
    print_and_log(
        f"Running in {'Development environment' if development else 'Production environment'}"
    )

    ctx.ensure_object(dict)
    ctx.obj["DEVELOPMENT"] = development
    if development:
        ctx.obj["FILTERED_URLS"] = urls["DEV"]
    else:
        ctx.obj["FILTERED_URLS"] = urls["PROD"]


@cli.command()
@click.option(
    "--webin-username",
    type=click.STRING,
    help="Username for webin authentication",
    envvar="WEBIN_USERNAME",
)
@click.option(
    "--metabolights-username",
    type=click.STRING,
    help="Username for MetaboLights metadata submission",
)
@click.option(
    "--credentials-file",
    type=click.File("r"),
    required=False,
    help="Name of a credentials file",
)
@click.argument("isa_json_file", type=click.File("r"))
@click.option(
    "--submit-to-biosamples",
    type=click.BOOL,
    default=True,
    help="Submit to BioSamples.",
)
@click.option("--submit-to-ena", type=click.BOOL, default=True, help="Submit to ENA.")
@click.option(
    "--file-transfer",
    type=click.Choice(["ftp", "aspera"], case_sensitive=False),
    required=True,
    default="ftp",
    help="provide the name of a file transfer solution, like ftp or aspera. The default is ftp.",
)
@click.option(
    "--data-folder",
    type=click.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        executable=True,
    ),
    multiple=True,
    help="Path to a folder holding the files you want to upload. All files in the folder (recursively) are included. Can be passed multiple times.",
)
@click.option(
    "--submit-to-metabolights",
    type=click.BOOL,
    default=True,
    help="Submit to Metabolights.",
)
@click.option(
    "--output",
    type=click.STRING,
    default=f"output_{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
)
@click.pass_context
def submit(
    ctx,
    webin_username,
    metabolights_username,
    credentials_file,
    isa_json_file,
    submit_to_biosamples,
    submit_to_ena,
    submit_to_metabolights,
    file_transfer,
    output,
    data_folder,
):
    """Start a submission to the target repositories."""
    target_repositories = []

    if submit_to_biosamples:
        target_repositories.append(TargetRepository.BIOSAMPLES.value)

    if submit_to_ena:
        target_repositories.append(TargetRepository.ENA.value)

    if submit_to_metabolights:
        target_repositories.append(TargetRepository.METABOLIGHTS.value)

    print_and_log(
        f"Starting submission of the ISA JSON to the target repositories: {', '.join(target_repositories)}."
    )

    urls_dict = ctx.obj["FILTERED_URLS"]

    data_file_paths = list_files_in_folders(data_folder)

    is_development = ctx.obj.get("DEVELOPMENT")
    if is_development is None:
        is_development = os.getenv("MARS_DEBUG") in ["1", 1]

    submission(
        webin_username,
        metabolights_username,
        credentials_file,
        isa_json_file.name,
        target_repositories,
        urls_dict,
        file_transfer,
        output,
        data_file_paths=data_file_paths,
        config=config,
        is_development=is_development,
    )


@cli.command()
@click.pass_context
def health_check(ctx):
    """Check the health of the target repositories."""
    print_and_log("Checking the health of the target repositories.")

    filtered_urls = ctx.obj["FILTERED_URLS"]
    for repo in ["WEBIN", "ENA", "BIOSAMPLES", "METABOLIGHTS"]:
        repo_url = filtered_urls[repo]["SERVICE"]
        try:
            health_response = requests.get(repo_url)
            if health_response.status_code != 200:
                print_and_log(
                    f"Could not reach service '{repo.lower()}' on this URL: '{repo_url}'. Status code: {health_response.status_code}. Content: {health_response.json()}",
                    level="error",
                )
            else:
                print_and_log(f"Service '{repo.lower()}' healthy and ready to use!")

        except requests.RequestException as err:
            tb = sys.exc_info()[2]
            print_and_log(
                f"Unexpected error for service '{repo.lower()}' on this URL: '{repo_url}'.\nError Trace:\n{err.with_traceback(tb)}",
                level="error",
            )
        except Exception as err:
            tb = sys.exc_info()[2]
            print_and_log(
                f"Unexpected error occurred.\nError Trace:\n{err.with_traceback(tb)}",
                level="error",
            )


@cli.command("validate-isa-json")
@click.argument(
    "isa_json_file",
    type=click.Path(exists=True),
)
def validate_investigation(isa_json_file):
    """Validate the ISA JSON file."""
    print_and_log(f"Validating {isa_json_file}.")

    try:
        investigation = load_investigation(isa_json_file)

        if isinstance(investigation, ValidationError):
            raise ValidationError

        print_and_log(f"ISA JSON with investigation '{investigation.title}' is valid.")
    except ValidationError as err:
        print_and_log(f"Validation errors occurred:\n{err}", level="error")


@cli.command("check-data-files")
@click.argument(
    "isa_json_file",
    type=click.File("r"),
)
@click.option(
    "--data-folder",
    type=click.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        executable=True,
    ),
    multiple=True,
    help="Path to a folder holding the data files. All files in the folder (recursively) are included. Can be passed multiple times.",
)
def check_data_files_command(isa_json_file, data_folder):
    """Check that the data files in the ISA JSON match the files in the data folders."""
    try:
        investigation = load_investigation(isa_json_file.name)

        if isinstance(investigation, ValidationError):
            raise investigation
    except ValidationError as err:
        print_and_log(f"Validation errors occurred:\n{err}", level="error")
        sys.exit(1)

    report = check_data_files(list_files_in_folders(data_folder), investigation)

    click.echo(
        report.to_text(
            f"Data file check for ISA JSON with investigation '{investigation.title}':"
        )
    )

    if report.missing or report.duplicates:
        sys.exit(1)


@cli.command()
@click.option(
    "--auth-provider",
    type=click.Choice(["webin", "metabolights"], case_sensitive=False),
    is_flag=False,
    flag_value="value",
    required=True,
    help="",
)
@click.argument(
    "username",
    type=click.STRING,
)
@click.option(
    "--password",
    type=click.STRING,
    hide_input=True,
    prompt=True,
    confirmation_prompt=True,
    help="The password to store. Note: You are required to confirm the password.",
)
def set_password(auth_provider, username, password):
    """Store a password in the keyring."""
    CredentialManager(auth_provider).set_password_keyring(username, password)


if __name__ == "__main__":
    cli()
