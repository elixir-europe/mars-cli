from __future__ import annotations

import re

from enum import Enum
from pydantic import BaseModel, Field, field_validator, ConfigDict
from mars_lib.target_repo import TargetRepository, TARGET_REPO_KEY


class IsaBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Comment(IsaBase):
    id: str | None = Field(alias="@id", default=None)
    name: str | None = None
    value: str | None = None


class CommentedIsaBase(IsaBase):
    comments: list[Comment] = []


class OntologySourceReference(CommentedIsaBase):
    description: str | None = None
    file: str | None = None
    name: str | None = None
    version: str | None = None


class Data(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    name: str | None = None
    type: str | None = None


class OntologyAnnotation(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    annotationValue: str | float | int | None = None
    termAccession: str | None = None
    termSource: str | None = Field(
        description="The abbreviated ontology name. It should correspond to one of the sources as specified in the ontologySourceReference section of the Investigation.",
        default=None,
    )


# TODO: QUESTION: comments field is not mentioned in the specs (https://isa-specs.readthedocs.io/en/latest/isajson.html#material-attribute-value-schema-json)
class MaterialAttributeValue(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    category: MaterialAttribute | None = None
    value: str | float | int | OntologyAnnotation | None = None
    unit: OntologyAnnotation | None = None


class Factor(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    factorName: str | None = None
    factorType: OntologyAnnotation | None = None


class FactorValue(IsaBase):
    id: str | None = Field(alias="@id", default=None)
    category: Factor | None = None
    value: str | float | int | OntologyAnnotation | None = None
    unit: OntologyAnnotation | None = None


# TODO: QUESTION: comments field is not mentioned in the specs (https://isa-specs.readthedocs.io/en/latest/isajson.html#material-attribute-value-schema-json)
class Source(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    characteristics: list[MaterialAttributeValue] = []
    name: str | None = None


# TODO: QUESTION: comments field is not mentioned in the specs (https://isa-specs.readthedocs.io/en/latest/isajson.html#material-attribute-value-schema-json)
class Sample(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    name: str | None = None
    characteristics: list[MaterialAttributeValue] = []
    factorValues: list[FactorValue] | None = None
    derivesFrom: list[Source] = []


class ProtocolParameter(IsaBase):
    id: str | None = Field(alias="@id", default=None)
    parameterName: OntologyAnnotation | None = None


class ProcessParameterValue(IsaBase):
    category: ProtocolParameter | None = None
    value: str | float | int | OntologyAnnotation | None = None
    unit: OntologyAnnotation | list[OntologyAnnotation] | None = None


# Helper class for protocol -> components
class Component(IsaBase):
    componentName: str | None = None
    componentType: OntologyAnnotation | None = None


class Protocol(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    components: list[Component] = []
    description: str | None = None
    name: str | None = None
    parameters: list[ProtocolParameter] = []
    protocolType: OntologyAnnotation | None = None
    uri: str | None = None
    version: str | None = None


class Material(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    characteristics: list[MaterialAttributeValue] = []
    name: str | None = None
    type: str | None = None
    derivesFrom: list[Material] = []

class Process(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    date: str | None = None
    executesProtocol: Protocol | None = None
    inputs: list[Source] | list[Sample] | list[Material] | list[Data] = []
    name: str | None = None
    nextProcess: Process | None = None
    outputs: list[Sample] | list[Material] | list[Data] = Field(
        default=[]
    )
    parameterValues: list[ProcessParameterValue] = []
    performer: str | None = None
    previousProcess: Process | None = None


# Helper for assay -> materials
class AssayMaterialType(IsaBase):
    samples: list[Sample] = []
    otherMaterials: list[Material] = []


class Assay(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    characteristicCategories: list[MaterialAttribute] = []
    dataFiles: list[Data] = []
    filename: str | None = None
    materials: AssayMaterialType | None = None
    measurementType: OntologyAnnotation | None = None
    processSequence: list[Process] = []
    technologyPlatform: str | None = None
    technologyType: OntologyAnnotation | None = None
    unitCategories: list[OntologyAnnotation] = []

    @field_validator("filename")
    def validate_filename(cls, v: str) -> str | None:
        if v is None:
            return v
        elif re.match(r"^a_", v):
            return v
        else:
            raise ValueError("'filename' should start with 'a_'")

    @field_validator("comments")
    def detect_target_repo_comments(cls, v: list[Comment]) -> list[Comment] | None:
        target_repo_comments = [
            comment for comment in v if comment.name == TARGET_REPO_KEY
        ]
        if len(target_repo_comments) == 0:
            raise ValueError(f"'{TARGET_REPO_KEY}' comment is missing")
        elif len(target_repo_comments) > 1:
            raise ValueError(f"Multiple '{TARGET_REPO_KEY}' comments found")
        else:
            if target_repo_comments[0].value in [
                item.value for item in TargetRepository
            ]:
                return v
            else:
                raise ValueError(
                    f"Invalid '{TARGET_REPO_KEY}' value: '{target_repo_comments[0].value}'"
                )


class Person(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    address: str | None = None
    affiliation: str | None = None
    email: str | None = None
    fax: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    midInitials: str | None = None
    phone: str | None = None
    roles: list[OntologyAnnotation] = []


class Publication(CommentedIsaBase):
    authorList: str | None = None
    doi: str | None = None
    pubMedID: str | None = None
    status: OntologyAnnotation | None = None
    title: str | None = None


class StudyMaterialType(IsaBase):
    sources: list[Source] = []
    samples: list[Sample] = []
    otherMaterials: list[Material] = []


class MaterialAttribute(IsaBase):
    id: str | None = Field(alias="@id", default=None)
    characteristicType: OntologyAnnotation | None = None


class Study(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    assays: list[Assay] = []
    characteristicCategories: list[MaterialAttribute] = []
    description: str | None = None
    factors: list[Factor] = []
    filename: str | None = None
    identifier: str | None = None
    materials: StudyMaterialType | None = None
    people: list[Person] = []
    processSequence: list[Process] = []
    protocols: list[Protocol] = []
    publicReleaseDate: str | None = None
    publications: list[Publication] = []
    studyDesignDescriptors: list[OntologyAnnotation] = []
    submissionDate: str | None = None
    title: str | None = None
    unitCategories: list[OntologyAnnotation] = []

    @field_validator("filename")
    def validate_filename(cls, v: str) -> str | None:
        if v is None:
            return v
        elif re.match(r"^s_", v):
            return v
        else:
            raise ValueError("'filename' should start with 's_'")


class Investigation(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    description: str | None = None
    filename: str | None = None
    identifier: str | None = None
    ontologySourceReferences: list[OntologySourceReference] = []
    people: list[Person] = []
    publicReleaseDate: str | None = None
    publications: list[Publication] = []
    studies: list[Study] = []
    submissionDate: str | None = None
    title: str | None = None

    @field_validator("filename")
    def validate_filename(cls, v: str) -> str | None:
        if v is None:
            return v
        elif re.match(r"^i_", v):
            return v
        else:
            raise ValueError("'filename' should start with 'i_'")
