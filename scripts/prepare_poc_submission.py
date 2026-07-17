#!/usr/bin/env python
"""
Prepare the PoC environments for MARS-CLI (no pytest, no run script):

- Ensure settings.ini exists in $MARS_SETTINGS_DIR/.mars, or ~/.mars
- Generate an ISA-JSON from each configured template, where:
    * dataFiles entries in the first assay are updated to point to
      UNIQUE .fastq.gz files
    * 'file name', 'file type', 'file checksum', 'checksum_method'
      comments are updated accordingly (MD5 over the .fastq.gz)
- Create poc_work/credentials.json from environment variables

Set ``HOLOFOOD_ISA_TEMPLATE_PATH`` to prepare the additional HoloFood
submission alongside ``ISA_TEMPLATE_PATH``.

The GitHub Action (or you, locally) will then call mars-cli using:

  ISA JSON:        poc_work/<submission>/isa.json
  Credentials:     poc_work/credentials.json
  Data files:      poc_work/<submission>/data/*.fastq.gz
"""


import json
import os
import textwrap
from pathlib import Path

from isa_generator import generate_isa_json_with_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_settings_ini() -> Path:
    parent_str = os.environ.get("MARS_SETTINGS_DIR")
    mars_dir = Path(parent_str) / ".mars"
    mars_dir.mkdir(parents=True, exist_ok=True)

    settings_path = mars_dir / "settings.ini"

    content = textwrap.dedent(
        f"""
        [logging]
        log_level = INFO
        log_file = {mars_dir / "app.log"}
        log_max_size = 1024
        log_max_files = 5

        [ena]
        development-url = http://localhost:8042/isaena
        development-submission-url = http://localhost:8042/isaena/submit

        [biosamples]
        development-url = http://localhost:8032/isabiosamples
        development-submission-url = http://localhost:8032/isabiosamples/submit

        [metabolights]
        development-url = https://www-test.ebi.ac.uk/metabolights/mars/ws3/submissions/
        development-submission-url = https://www-test.ebi.ac.uk/metabolights/mars/ws3/submissions/
        development-token-url = https://www-test.ebi.ac.uk/metabolights/mars/ws3/auth/token
        """
    ).strip() + "\n"

    settings_path.write_text(content)

    return settings_path


def write_credentials_json(work_dir: Path) -> Path:
    required_vars = [
        "WEBIN_USERNAME",
        "WEBIN_PASSWORD",
        # "METABOLIGHTS_METADATA_USERNAME",
        # "METABOLIGHTS_METADATA_PASSWORD",
        # "METABOLIGHTS_DATA_USERNAME",
        # "METABOLIGHTS_DATA_PASSWORD",
    ]

    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Missing environment variables for credentials: {', '.join(missing)}"
        )

    creds = {
        "webin": {
            "username": os.environ["WEBIN_USERNAME"],
            "password": os.environ["WEBIN_PASSWORD"],
        },
        # "metabolights_metadata": {
        #     "username": os.environ["METABOLIGHTS_METADATA_USERNAME"],
        #     "password": os.environ["METABOLIGHTS_METADATA_PASSWORD"],
        # },
        # "metabolights_data": {
        #     "username": os.environ["METABOLIGHTS_DATA_USERNAME"],
        #     "password": os.environ["METABOLIGHTS_DATA_PASSWORD"],
        # },
    }

    cred_path = work_dir / "credentials.json"
    cred_path.write_text(json.dumps(creds, indent=2))
    return cred_path


def resolve_isa_template() -> Path:
    template_env = os.environ.get("ISA_TEMPLATE_PATH")
    if template_env:
        src = Path(template_env)
    else:
        src = PROJECT_ROOT.parent / "MARS" / "test-data" / "biosamples-input-isa.json"

    if not src.exists():
        raise FileNotFoundError(f"ISA template not found at {src}")

    return src


def main() -> None:
    settings_path = ensure_settings_ini()

    work_dir = PROJECT_ROOT / "poc_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    isa_templates = {"multi-omics": resolve_isa_template()}
    holofood_template = os.environ.get("HOLOFOOD_ISA_TEMPLATE_PATH")
    if holofood_template:
        holofood_path = Path(holofood_template)
        if not holofood_path.exists():
            raise FileNotFoundError(f"HoloFood ISA template not found at {holofood_path}")
        isa_templates["holofood"] = holofood_path

    prepared_submissions = []
    for submission_name, isa_template in isa_templates.items():
        submission_dir = work_dir / submission_name
        isa_path, data_files = generate_isa_json_with_data(
            work_dir=submission_dir,
            template_path=isa_template,
        )
        prepared_submissions.append((isa_template, isa_path, data_files))

    cred_path = write_credentials_json(work_dir)

    print(f"[MARS-POC] settings.ini:    {settings_path}")
    print(f"[MARS-POC] work dir:        {work_dir}")
    print(f"[MARS-POC] credentials:     {cred_path}")
    for isa_template, isa_path, data_files in prepared_submissions:
        print(f"[MARS-POC] template:        {isa_template}")
        print(f"[MARS-POC] ISA JSON file:   {isa_path}")
        print("[MARS-POC] data files:")
        for data_file in data_files:
            print(f"  - {data_file}")


if __name__ == "__main__":
    main()
