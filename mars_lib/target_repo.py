from enum import Enum


TARGET_REPO_KEY = "target_repository"


class TargetRepository(str, Enum):
    """
    Holds constants, tied to the target repositories.
    """

    ENA = "ena"
    METABOLIGHTS = "metabolights"
    BIOSAMPLES = "biosamples"
    EVA = "eva"
    ARRAYEXPRESS = "arrayexpress"

    @classmethod
    def available_repositories(cls):
        return {item.value for item in cls}

    @classmethod
    def get_repository_urls_from_config(
        cls, config
    ) -> dict[str, dict[str, dict[str, str]]]:
        return {
            "DEV": {
                "ENA": {
                    "SERVICE": config.get(
                        "ena",
                        "development-url",
                        fallback="https://wwwdev.ebi.ac.uk/biosamples/samples",
                    ),
                    "SUBMISSION": config.get(
                        "ena",
                        "development-submission-url",
                        fallback="https://wwwdev.ebi.ac.uk/biosamples/samples/submit",
                    ),
                    "DATA-SUBMISSION": config.get(
                        "ena",
                        "development-data-submission-url",
                        fallback="webin2.ebi.ac.uk",
                    ),
                    "STUDY-PAGE": config.get(
                        "ena",
                        "development-study-page-url",
                        fallback="https://wwwdev.ebi.ac.uk/ena/browser/view/",
                    ),
                },
                "WEBIN": {
                    "SERVICE": config.get(
                        "webin",
                        "development-url",
                        fallback="https://wwwdev.ebi.ac.uk/ena/submit/webin/auth",
                    ),
                    "TOKEN": config.get(
                        "webin",
                        "development-token-url",
                        fallback="https://wwwdev.ebi.ac.uk/ena/submit/webin/auth/token",
                    ),
                },
                "METABOLIGHTS": {
                    "SERVICE": config.get(
                        "metabolights",
                        "development-url",
                        fallback="https://wwwdev.ebi.ac.uk/metabolights/test/ws3/version",
                    ),
                    "SUBMISSION": config.get(
                        "metabolights",
                        "development-submission-url",
                        fallback="https://wwwdev.ebi.ac.uk/metabolights/test/ws3/submissions/",
                    ),
                    "TOKEN": config.get(
                        "metabolights",
                        "development-token-url",
                        fallback="https://wwwdev.ebi.ac.uk/metabolights/test/iam/realms/metabolights/protocol/openid-connect/token",
                    ),
                    "STUDY-PAGE": config.get(
                        "metabolights",
                        "development-study-page-url",
                        fallback="https://www-test.ebi.ac.uk/metabolights/",
                    ),
                },
                "BIOSAMPLES": {
                    "SERVICE": config.get(
                        "biosamples",
                        "development-url",
                        fallback="https://wwwdev.ebi.ac.uk/biosamples/samples/",
                    ),
                    "SUBMISSION": config.get(
                        "biosamples",
                        "development-submission-url",
                        fallback="https://wwwdev.ebi.ac.uk/biosamples/samples/",
                    ),
                    # BioSamples itself, to read and update records. Not configurable:
                    # the URLs above can point to the MARS BioSamples service instead.
                    "SAMPLES": "https://wwwdev.ebi.ac.uk/biosamples/samples/",
                },
            },
            "PROD": {
                "ENA": {
                    "SERVICE": config.get(
                        "ena",
                        "production-url",
                        fallback="https://www.ebi.ac.uk/ena/submit/webin-v2/",
                    ),
                    "SUBMISSION": config.get(
                        "ena",
                        "production-submission-url",
                        fallback="https://www.ebi.ac.uk/ena/submit/drop-box/submit/?auth=ENA",
                    ),
                    "DATA-SUBMISSION": config.get(
                        "ena",
                        "development-data-submission-url",
                        fallback="webin2.ebi.ac.uk",
                    ),
                    "STUDY-PAGE": config.get(
                        "ena",
                        "production-study-page-url",
                        fallback="https://www.ebi.ac.uk/ena/browser/view/",
                    ),
                },
                "WEBIN": {
                    "SERVICE": config.get(
                        "webin",
                        "production-url",
                        fallback="https://wwwdev.ebi.ac.uk/ena/dev/submit/webin/auth",
                    ),
                    "TOKEN": config.get(
                        "webin",
                        "production-token-url",
                        fallback="https://wwwdev.ebi.ac.uk/ena/dev/submit/webin/auth/token",
                    ),
                },
                "METABOLIGHTS": {
                    "SERVICE": config.get(
                        "metabolights",
                        "production-url",
                        fallback="https://www.ebi.ac.uk/metabolights/ws3/",
                    ),
                    "SUBMISSION": config.get(
                        "metabolights",
                        "production-submission-url",
                        fallback="https://www.ebi.ac.uk/metabolights/ws3/submissions/provisional-studies",
                    ),
                    "TOKEN": config.get(
                        "metabolights",
                        "production-token-url",
                        fallback="https://www.ebi.ac.uk/metabolights/iam/realms/metabolights/protocol/openid-connect/token",
                    ),
                    "STUDY-PAGE": config.get(
                        "metabolights",
                        "production-study-page-url",
                        fallback="https://www.ebi.ac.uk/metabolights/",
                    ),
                },
                "BIOSAMPLES": {
                    "SERVICE": config.get(
                        "biosamples",
                        "production-url",
                        fallback="https://www.ebi.ac.uk/biosamples/samples/",
                    ),
                    "SUBMISSION": config.get(
                        "biosamples",
                        "production-submission-url",
                        fallback="https://www.ebi.ac.uk/biosamples/samples/",
                    ),
                    "SAMPLES": "https://www.ebi.ac.uk/biosamples/samples/",
                },
            },
        }
