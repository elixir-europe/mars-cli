import json

import pytest
from pathlib import Path
import ftplib

from mars_lib.ftp_upload import FTPUploader


def test_upload_login_failure():
    uploader = FTPUploader("webin2.ebi.ac.uk", "junk", "more junk")
    with pytest.raises(ftplib.error_perm, match="530 Login incorrect."):
        uploader.upload([Path("./tests/fixtures/not_a_json_file.txt")])


def test_upload_creates_missing_target_folder(monkeypatch):
    calls = []

    class FakeFTP:
        context = type("Context", (), {"set_ciphers": lambda self, ciphers: None})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect(self, host, port, timeout):
            pass

        def login(self, username, password):
            pass

        def prot_p(self):
            pass

        def cwd(self, path):
            calls.append(("cwd", path))
            if ("mkd", path) not in calls:
                raise ftplib.error_perm("550 No such directory")

        def mkd(self, path):
            calls.append(("mkd", path))

        def nlst(self):
            return []

        def storbinary(self, cmd, file):
            calls.append(("stor", cmd))

    monkeypatch.setattr("mars_lib.ftp_upload.PatchFTP_TLS", FakeFTP)

    FTPUploader("ftp.test", "user", "secret").upload(
        [Path("./tests/fixtures/not_a_json_file.txt")],
        target_location="/upload/RAW_FILES",
    )

    assert calls == [
        ("cwd", "/upload/RAW_FILES"),
        ("mkd", "/upload/RAW_FILES"),
        ("cwd", "/upload/RAW_FILES"),
        ("stor", "STOR not_a_json_file.txt"),
    ]


@pytest.mark.skip(
    reason="Relies on real ENA credentials in test_credentials_example.json"
)
def test_upload_success():
    # For local testing, add ENA username/password to test_credentials_example.json
    with open("./tests/test_credentials_example.json") as f:
        creds = json.load(f)
    uploader = FTPUploader("webin2.ebi.ac.uk", creds["username"], creds["password"])
    uploader.upload(
        [
            Path("MARS/test-data/ENA_TEST2.R2.fastq.gz"),
            Path("./tests/fixtures/not_a_json_file.txt"),
        ]
    )
