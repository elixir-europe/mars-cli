import json
from pydantic import BaseModel, field_validator, Field, ConfigDict
import pydantic.alias_generators
from mars_lib.target_repo import TargetRepository


class Filter(BaseModel):
    key: str
    value: str


class Path(BaseModel):
    key: str
    where: Filter | None = None


class Accession(BaseModel):
    path: list[Path] = []
    value: str

    @field_validator("path")
    def validate_path(cls, path: list[Path]) -> list[Path]:
        keys = [p.key for p in path]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate keys found in path list")
        return path


class Error(BaseModel):
    type: str
    message: str
    path: list[Path] = []


class Info(BaseModel):
    name: str | None = None
    message: str


class RepositoryResponse(BaseModel):
    # This is a Pydantic configuration that will convert the field names to camel case and be accessible as alias.
    model_config = ConfigDict(alias_generator=pydantic.alias_generators.to_camel)

    target_repository: str = Field(alias="targetRepository")
    accessions: list[Accession] = []
    errors: list[Error] = []
    info: list[Info] = []
    submission_status: None | str = None
    task_id: None | str = None
    submission_id: None | str = None

    @field_validator("target_repository")
    def validate_target_repository(cls, v: str) -> str:
        if v not in [item.value for item in TargetRepository]:
            raise ValueError(f"Invalid 'targetRepository' value: '{v}'")
        return v

    @classmethod
    def from_json_file(cls, json_file_path: str) -> "RepositoryResponse":
        with open(json_file_path, "r") as file:
            data = json.load(file)

        return cls.model_validate(data)

    @classmethod
    def from_json(cls, json_string: str) -> "RepositoryResponse":
        data = json.loads(json_string)

        return cls.model_validate(data)
