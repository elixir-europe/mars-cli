from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mars_lib.target_repo import TARGET_REPO_KEY, TargetRepository


class IsaBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IsaReference(IsaBase):
    """
    A reference to an object defined elsewhere in the ISA JSON, by '@id'
    (e.g. process inputs/outputs, 'executesProtocol', 'derivesFrom').
    """

    id: str = Field(alias="@id")
    name: str | None = None
    type: str | None = None


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
    id: str = Field(alias="@id")
    name: str
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
    category: MaterialAttribute
    value: str | float | int | OntologyAnnotation | None = None
    unit: OntologyAnnotation | None = None


class Factor(CommentedIsaBase):
    id: str | None = Field(alias="@id", default=None)
    factorName: str | None = None
    factorType: OntologyAnnotation | None = None


class FactorValue(IsaBase):
    id: str | None = Field(alias="@id", default=None)
    category: Factor
    value: str | float | int | OntologyAnnotation | None = None
    unit: OntologyAnnotation | None = None


# TODO: QUESTION: comments field is not mentioned in the specs (https://isa-specs.readthedocs.io/en/latest/isajson.html#material-attribute-value-schema-json)
class Source(CommentedIsaBase):
    id: str = Field(alias="@id")
    characteristics: list[MaterialAttributeValue]
    name: str


# TODO: QUESTION: comments field is not mentioned in the specs (https://isa-specs.readthedocs.io/en/latest/isajson.html#material-attribute-value-schema-json)
class Sample(CommentedIsaBase):
    id: str = Field(alias="@id")
    name: str
    characteristics: list[MaterialAttributeValue] = Field(min_length=1)
    factorValues: list[FactorValue] | None = None
    derivesFrom: list[IsaReference] = Field(min_length=1)


class ProtocolParameter(IsaBase):
    id: str = Field(alias="@id")
    parameterName: OntologyAnnotation | str


class ProcessParameterValue(IsaBase):
    category: IsaReference
    value: str | float | int | OntologyAnnotation | None = None
    unit: OntologyAnnotation | None = None


# Helper class for protocol -> components
class Component(IsaBase):
    componentName: str | None = None
    componentType: OntologyAnnotation | None = None


class Protocol(CommentedIsaBase):
    id: str = Field(alias="@id")
    components: list[Component] = []
    description: str | None = None
    name: str
    parameters: list[ProtocolParameter] = []
    protocolType: OntologyAnnotation | None = None
    uri: str | None = None
    version: str | None = None


class Material(CommentedIsaBase):
    id: str = Field(alias="@id")
    characteristics: list[MaterialAttributeValue] = Field(min_length=1)
    name: str
    type: str | None = None


class Process(CommentedIsaBase):
    id: str = Field(alias="@id")
    date: str | None = None
    executesProtocol: IsaReference | Protocol
    inputs: (
        list[IsaReference] | list[Source] | list[Sample] | list[Material] | list[Data]
    ) = Field(min_length=1)
    name: str
    nextProcess: IsaReference | Process | None = None
    outputs: list[IsaReference] | list[Sample] | list[Material] | list[Data] = Field(
        min_length=1
    )
    parameterValues: list[ProcessParameterValue] = []
    performer: str | None = None
    previousProcess: IsaReference | Process | None = None

    @field_validator("previousProcess", "nextProcess", mode="before")
    def empty_link_is_no_link(cls, v: object) -> object:
        # The first/last process of a sequence has no previous/next process,
        # which is written as an empty object ('{}').
        return None if v == {} else v


# Helper for assay -> materials
class AssayMaterialType(IsaBase):
    samples: list[IsaReference] = Field(min_length=1)
    otherMaterials: list[Material] = []


ARC_FILENAMES = {
    "investigation": r"isa\.investigation\.xlsx",
    "study": r"studies/[^/\\]+/isa\.study\.xlsx",
    "assay": r"assays/[^/\\]+/isa\.assay\.xlsx",
}

ARC_FILENAME_EXAMPLES = {
    "investigation": "isa.investigation.xlsx",
    "study": "studies/<name>/isa.study.xlsx",
    "assay": "assays/<name>/isa.assay.xlsx",
}


def _is_valid_filename(filename: str, prefix: str, arc_kind: str) -> bool:
    """
    Accept ISA-Tab style names (for example 's_study.txt') and names that follow
    the ARC layout (for example 'studies/my_study/isa.study.xlsx').
    """
    return filename.startswith(f"{prefix}_") or bool(
        re.fullmatch(ARC_FILENAMES[arc_kind], filename)
    )


def _validate_filename(filename: str | None, prefix: str, arc_kind: str) -> str:
    if filename is None or _is_valid_filename(filename, prefix, arc_kind):
        return filename
    raise ValueError(
        f"'filename' should start with '{prefix}_' "
        f"or be '{ARC_FILENAME_EXAMPLES[arc_kind]}'"
    )


class Assay(CommentedIsaBase):
    id: str = Field(alias="@id")
    characteristicCategories: list[MaterialAttribute] = Field(min_length=1)
    dataFiles: list[Data]
    filename: str
    materials: AssayMaterialType
    measurementType: OntologyAnnotation | None = None
    processSequence: list[Process] = Field(min_length=1)
    technologyPlatform: str | None = None
    technologyType: OntologyAnnotation | None = None
    unitCategories: list[OntologyAnnotation] = []

    @field_validator("filename")
    def validate_filename(cls, v: str) -> str:
        return _validate_filename(v, "a", "assay")

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
    sources: list[Source] = Field(min_length=1)
    samples: list[Sample] = Field(min_length=1)
    otherMaterials: list[Material] = []


class MaterialAttribute(IsaBase):
    id: str = Field(alias="@id")
    characteristicType: OntologyAnnotation | None = None


class Study(CommentedIsaBase):
    id: str = Field(alias="@id")
    assays: list[Assay] = []
    characteristicCategories: list[MaterialAttribute] = Field(min_length=1)
    description: str | None = None
    factors: list[Factor] = []
    filename: str
    identifier: str | None = None
    materials: StudyMaterialType
    people: list[Person] = []
    processSequence: list[Process] = Field(min_length=1)
    protocols: list[Protocol] = Field(min_length=1)
    publicReleaseDate: str | None = None
    publications: list[Publication] = []
    studyDesignDescriptors: list[OntologyAnnotation] = []
    submissionDate: str | None = None
    title: str
    unitCategories: list[OntologyAnnotation] = []

    @field_validator("filename")
    def validate_filename(cls, v: str) -> str:
        return _validate_filename(v, "s", "study")

    @field_validator("characteristicCategories")
    def require_organism_category(
        cls, v: list[MaterialAttribute]
    ) -> list[MaterialAttribute]:
        # Special rule: the 'organism' characteristic category is mandatory
        # (required for BioSamples submissions).
        if not any(
            category.characteristicType is not None
            and isinstance(category.characteristicType.annotationValue, str)
            and category.characteristicType.annotationValue.lower() == "organism"
            for category in v
        ):
            raise ValueError("'organism' characteristic category is missing")
        return v


class Investigation(CommentedIsaBase):
    id: str = Field(alias="@id")
    description: str | None = None
    filename: str
    identifier: str | None = None
    ontologySourceReferences: list[OntologySourceReference] = []
    people: list[Person] = []
    publicReleaseDate: str | None = None
    publications: list[Publication] = []
    studies: list[Study] = Field(min_length=1)
    submissionDate: str | None = None
    title: str

    @field_validator("filename")
    def validate_filename(cls, v: str) -> str:
        return _validate_filename(v, "i", "investigation")
