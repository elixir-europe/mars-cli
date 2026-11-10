#!/usr/bin/env python3

import requests
import json
import re
import os
from pathlib import Path
from jsonschema import validate
from jsonschema.exceptions import ValidationError, SchemaError
from typing import Union, Any, Optional, List

# -- #
# Hardcoded values
# -- #
biosamples_endpoints = {
    "prod": "https://www.ebi.ac.uk/biosamples/samples/",
    "dev": "https://wwwdev.ebi.ac.uk/biosamples/samples/",
}
webin_token_endpoints = {
    "prod": "https://www.ebi.ac.uk/ena/submit/webin/auth/token",
    "dev": "https://wwwdev.ebi.ac.uk/ena/dev/submit/webin/auth/token",
}
# Resolved relative to this module so it works from any working directory and
# from an installed package.
input_json_schema_filepath = str(Path(__file__).parent / "biosamples-input-schema.json")


# -- #
# Code blocks
# -- #
def load_json_file(file: str) -> Any:
    """
    Function to load a JSON file as a dictionary.
    Args:
        file (str): Path to the file to be loaded.
    """
    if not os.path.exists(file):
        raise FileNotFoundError(f"The file '{file}' does not exist.")
    if not os.path.isfile(file):
        raise ValueError(f"The path '{file}' is not a file.")
    if not file.endswith(".json"):
        raise ValueError(
            f"The given file '{file}' is not a JSON file based on its extension."
        )

    try:
        with open(file, "r") as f:
            loaded_dict = json.load(f)
        return loaded_dict
    except json.JSONDecodeError:
        raise ValueError(
            f"The file content of the given file '{file}' is not valid JSON."
        )


def handle_input_dict(input: Union[dict[str, Any], str]) -> dict[str, Any]:
    """
    Function to handle the input: assert that it's either a dictionary or
        the filepath to an existing file containing the dictionary

    Args:
        input (dict or str): Dictionary or filepath to the dictionary JSON.
    """
    if isinstance(input, dict):
        return input
    else:
        try:
            loaded_dict = load_json_file(input)

            if not isinstance(loaded_dict, dict):
                raise ValueError(
                    f"The file '{input}' does not contain a valid dictionary."
                )

            return loaded_dict

        # TODO: Remove this because the json.JSONDecodeError is already caught in load_json_file
        # In the meantime, it doesn't break anything.
        except json.JSONDecodeError:
            raise ValueError(f"The file '{input}' is not a valid JSON file.")


def get_header(token: str) -> dict[str, str]:
    """
    Obtain the header using a token.

    Args:
    token (str): The Webin auth token.

    Returns:
    dict: The header.
    """
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/hal+json",
        "Authorization": f"Bearer {token}",
    }


def validate_bs_accession(accession_str: str) -> None:
    """
    Validates that the given accession string conforms to the specified regex format.
        See: https://registry.identifiers.org/registry/biosample

    Args:
    accession_str (str): The accession string to be validated.
    """

    pattern = r"^SAM[NED](\w)?\d+$"

    if not re.match(pattern, accession_str):
        raise ValueError(
            f"The provided accession string '{accession_str}' does not match the required format."
        )


def validate_json_against_schema(
    json_doc: Union[dict[str, List[str]], str], json_schema: Union[dict[str, str], str]
) -> Optional[bool]:
    """
    Validates a JSON document against a given JSON Schema.

    Args:
    json_filepath (dict, str): JSON document or the filepath to it.
    schema_filepath (dict, str): JSON schema or the filepath to it.
    """
    # Load both files if needed
    if isinstance(json_doc, dict):
        json_data = json_doc
    else:
        json_data = load_json_file(json_doc)

    if isinstance(json_schema, dict):
        schema_data = json_schema
    else:
        schema_data = load_json_file(json_schema)

    # Validating JSON against the schema
    try:
        validate(instance=json_data, schema=schema_data)
        return True
    except ValidationError as e:
        raise ValidationError(
            f"Found an error when validating the JSON document with its JSON Schema. This may mean that the given input is invalid."
            f"JSON validation error: {e.message}"
        )
    except SchemaError as e:
        raise SchemaError(f"Schema error: {e.message}")


def _ext_ref_key(ext_ref: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """
    Identity of an external reference, matching how BioSamples compares them:
        same url and same set of DUO codes. A missing "duo" equals an empty one.
    """
    return ext_ref["url"], tuple(sorted(ext_ref.get("duo") or []))


class BiosamplesRecord:
    """
    Class representing a record for biosamples to be extended.

    Attributes:
    biosamples_externalReferences: dict or filepath to external references
    production: boolean indicating environment mode
    """

    def __init__(self, bs_accession: str) -> None:
        """
        Initialize the BiosamplesRecord with provided arguments.

        Args:
        bs_accession: a valid Biosamples accession (e.g. SAMEA112654119)
        """
        validate_bs_accession(bs_accession)
        self.bs_accession = bs_accession
        self.biosamples_credentials: Optional[dict[str, str]] = None
        self.biosamples_externalReferences: List[str] = []
        self.production: bool = False
        self.bs_json: dict[str, Any] = {}
        self.biosamples_endpoint: Optional[str] = None

    def _get_endpoint(self) -> str:
        """
        The endpoint the record was fetched from, otherwise the one matching `production`.
        """
        if self.biosamples_endpoint:
            return self.biosamples_endpoint
        return biosamples_endpoints["prod" if self.production else "dev"]

    def display(self) -> None:
        """
        Display the attributes for demonstration purposes.
        """
        print("Biosamples Credentials:", self.biosamples_credentials)
        print("Biosamples External References:", self.biosamples_externalReferences)
        print("Production Mode:", self.production)

    def fetch_bs_json(
        self, biosamples_endpoint: str, header: Optional[dict[str, str]] = None
    ) -> Optional[dict[str, Any]]:
        """
        Fetches the BioSample's record (JSON) of the accession.

        Args:
            biosamples_endpoint (str): The endpoint to be used to fetch the record's JSON.
            header (dict, optional): HTTP headers for the request. Public records can be
                fetched anonymously, private ones need the submitter's Authorization header.
        """

        self.biosamples_endpoint = biosamples_endpoint
        self.biosamples_url = f"{biosamples_endpoint}{self.bs_accession}.json"

        try:
            r = requests.get(self.biosamples_url, headers=header)

            if r.status_code != 200:
                raise RuntimeError(
                    f"Expected status code 200, but received {r.status_code}. Used URL: '{self.biosamples_url}'. Response content: {r.text}"
                )

            # Attempt to load the JSON content
            response_json = r.json()
            if not isinstance(response_json, dict):
                raise ValueError(
                    f"The response content is not a valid dictionary. Content: {r.text}"
                )

        except requests.RequestException as e:
            raise RuntimeError(f"Error making the request. Details: {e}")

        except json.JSONDecodeError:
            raise ValueError(
                f"The server response is not valid JSON. Content: {r.text}"
            )

        self.bs_json = response_json
        return self.bs_json

    def load_bs_json(
        self, bs_json: Union[str, dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        """
        Loads a given JSON, or the file containing it, as the BioSample's record (JSON) for this instance.
            It is an alternative to fetching it directly from BioSample.

        Args:
            bs_json Union[str, dict]: The already Biosamples JSON metadata of the accession either path to file or dictionary.
        """
        if isinstance(bs_json, dict):
            self.bs_json = bs_json
            return self.bs_json
        elif isinstance(bs_json, str):
            bs_json_data = load_json_file(bs_json)
            self.bs_json = bs_json_data
            return self.bs_json
        else:
            raise ValueError(
                "Neither the file containing the Biosamples JSON nor the Biosamples JSON itself were given to load it into the instance."
            )

    def pop_links(self) -> dict[str, Any]:
        """
        Removes "_links" array (which is added automatically after updating the biosamples on the BioSample's side).
        """

        if "_links" in self.bs_json:
            self.bs_json.pop("_links")

        return self.bs_json

    def extend_externalReferences(
        self, new_ext_refs_list: List[dict[str, str]]
    ) -> dict[str, Any]:
        """
        Extends the JSON of the BioSample's record with new externalReferences.
            Existing references keep their order, new ones are appended and
            references already present on the record are skipped.
        """
        if not self.bs_json:
            self.fetch_bs_json(self._get_endpoint())
        self.pop_links()

        ext_refs_list: List[dict[str, Any]] = []
        seen_refs: set[tuple[str, tuple[str, ...]]] = set()
        for ext_ref in self.bs_json.get("externalReferences", []) + new_ext_refs_list:
            key = _ext_ref_key(ext_ref)
            if key not in seen_refs:
                seen_refs.add(key)
                ext_refs_list.append(ext_ref)

        self.bs_json["externalReferences"] = ext_refs_list
        return self.bs_json

    def update_remote_record(
        self, header: dict[str, str], webin_auth: str = "?authProvider=WEBIN"
    ) -> Optional[str]:
        """
        Updates the remote record of the BioSample's accession with the current sample JSON.

        Args:
            header (dict): The HTTP headers to use in the request.
            webin_auth (str, optional): The authentication provider for WEBIN.
        """
        update_url = f"{self._get_endpoint()}{self.bs_accession}{webin_auth}"
        updated_json = json.dumps(self.bs_json)

        try:
            r = requests.put(update_url, headers=header, data=updated_json)

            # Check if HTTP status code indicates success (2xx range)
            if r.status_code != 200:
                raise RuntimeError(
                    f"Expected status code 200, but received {r.status_code}. Response content: {r.text}"
                )

        except requests.RequestException as e:
            raise RuntimeError(f"Error making the request. Details: {e}")

        return r.text
