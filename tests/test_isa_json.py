import re


from mars_lib.isa_json import (
    reduce_investigation_for_target_repo,
    load_investigation,
    update_investigation,
    map_data_files_to_repositories,
    check_data_files,
    is_assay_for_target_repo,
)
from mars_lib.target_repo import TargetRepository, TARGET_REPO_KEY
import pytest
from pydantic import ValidationError
from mars_lib.models.isa_json import (
    Data,
    Material,
    Assay,
    Person,
    Investigation,
    IsaReference,
    MaterialAttribute,
    MaterialAttributeValue,
    OntologyAnnotation,
    OntologySourceReference,
    Process,
    Publication,
    Study,
    _is_valid_filename,
)
from mars_lib.models.repository_response import RepositoryResponse
import json
from pathlib import Path


ENA_MINIMAL_ISA = "MARS/test-data/ena-minimal.json"
METABOLIGHTS_ISA = "MARS/test-data/metabolights-lcms.json"
ENA_COMPLETE_ISA = "MARS/test-data/ena-complete.json"
# One study with an ENA and a MetaboLights assay, that each use another sample
HOLOFOOD_ISA = "MARS/test-data/HoloFoodinISA_v1.0.json"


def _multi_repository_investigation():
    """The MetaboLights LC-MS study with the ENA sequencing assay added, so one
    investigation holds assays for two target repositories."""
    investigation = load_investigation(METABOLIGHTS_ISA)
    ena_assay = load_investigation(ENA_COMPLETE_ISA).studies[0].assays[0]
    investigation.studies[0].assays.append(ena_assay)
    return investigation


# --- Builders for minimal documents that satisfy the (strict) MARS model ---
def minimal_process(**overrides):
    process = {
        "@id": "#process/1",
        "name": "process 1",
        "executesProtocol": {"@id": "#protocol/1"},
        "inputs": [{"@id": "#in/1"}],
        "outputs": [{"@id": "#out/1"}],
    }
    return {**process, **overrides}


def minimal_assay(**overrides):
    assay = {
        "@id": "#assay/1",
        "filename": "a_assay.txt",
        "comments": [{"name": TARGET_REPO_KEY, "value": TargetRepository.ENA.value}],
        "characteristicCategories": [{"@id": "#cc/1"}],
        "dataFiles": [],
        "materials": {"samples": [{"@id": "#sample/1"}]},
        "processSequence": [minimal_process()],
    }
    return {**assay, **overrides}


def minimal_study(**overrides):
    characteristic = {"category": {"@id": "#cc/organism"}}
    study = {
        "@id": "#study/1",
        "filename": "s_study.txt",
        "title": "study 1",
        "characteristicCategories": [
            {
                "@id": "#cc/organism",
                "characteristicType": {"annotationValue": "organism"},
            }
        ],
        "protocols": [{"@id": "#protocol/1", "name": "protocol 1"}],
        "materials": {
            "sources": [
                {
                    "@id": "#source/1",
                    "name": "source 1",
                    "characteristics": [characteristic],
                }
            ],
            "samples": [
                {
                    "@id": "#sample/1",
                    "name": "sample 1",
                    "characteristics": [characteristic],
                    "derivesFrom": [{"@id": "#source/1"}],
                }
            ],
        },
        "processSequence": [minimal_process()],
        "assays": [minimal_assay()],
    }
    return {**study, **overrides}


def minimal_investigation(**overrides):
    investigation = {
        "@id": "#investigation/1",
        "filename": "i_investigation.txt",
        "title": "investigation 1",
        "studies": [minimal_study()],
    }
    return {**investigation, **overrides}


def minimal(model, **overrides):
    builder = {
        Investigation: minimal_investigation,
        Study: minimal_study,
        Assay: minimal_assay,
    }[model]
    return builder(**overrides)


def test_minimal_documents_are_valid():
    assert Investigation.model_validate(minimal_investigation())


@pytest.mark.parametrize(
    "model, field",
    [
        (Investigation, "@id"),
        (Investigation, "filename"),
        (Investigation, "title"),
        (Investigation, "studies"),
        (Study, "@id"),
        (Study, "filename"),
        (Study, "title"),
        (Study, "protocols"),
        (Study, "materials"),
        (Study, "processSequence"),
        (Study, "characteristicCategories"),
        (Assay, "@id"),
        (Assay, "filename"),
        (Assay, "materials"),
        (Assay, "dataFiles"),
        (Assay, "processSequence"),
    ],
)
def test_mandatory_fields_are_required(model, field):
    document = minimal(model)
    del document[field]
    with pytest.raises(ValidationError) as error:
        model.model_validate(document)
    assert {"type": "missing", "loc": (field,)}.items() <= {
        "type": error.value.errors()[0]["type"],
        "loc": error.value.errors()[0]["loc"],
    }.items()


@pytest.mark.parametrize(
    "model, field",
    [
        (Investigation, "studies"),
        (Study, "protocols"),
        (Study, "processSequence"),
        (Study, "characteristicCategories"),
        (Assay, "processSequence"),
    ],
)
def test_one_to_n_lists_cannot_be_empty(model, field):
    with pytest.raises(ValidationError, match="at least 1 item"):
        model.model_validate(minimal(model, **{field: []}))


def test_assay_characteristic_categories_are_optional():
    without_key = minimal(Assay)
    del without_key["characteristicCategories"]

    assert Assay.model_validate(without_key).characteristicCategories == []
    assert (
        Assay.model_validate(minimal(Assay, characteristicCategories=[]))
        .characteristicCategories
        == []
    )


def test_study_requires_organism_characteristic_category():
    with pytest.raises(ValidationError, match="'organism' characteristic category"):
        Study.model_validate(
            minimal_study(characteristicCategories=[{"@id": "#cc/other"}])
        )


def test_process_links_may_be_empty_objects():
    # The first/last process of a sequence has no previous/next process
    process = Process.model_validate(
        minimal_process(previousProcess={}, nextProcess={"@id": "#process/2"})
    )
    assert process.previousProcess is None
    assert process.nextProcess.id == "#process/2"


def test_process_inputs_and_outputs_are_optional():
    # A step split over two linked processes: inputs on the first, outputs on the second
    assert Process.model_validate(minimal_process(outputs=[])).outputs == []
    assert Process.model_validate(minimal_process(inputs=[])).inputs == []
    # A process may also have neither, whether empty or left out
    process = Process.model_validate(minimal_process(inputs=[], outputs=[]))
    assert process.inputs == [] and process.outputs == []
    process = minimal_process()
    del process["inputs"], process["outputs"]
    process = Process.model_validate(process)
    assert process.inputs == [] and process.outputs == []


def test_process_inputs_and_outputs_are_references():
    with pytest.raises(ValidationError):
        Process.model_validate(minimal_process(inputs=[{"name": "no id"}]))
    with pytest.raises(ValidationError):
        Process.model_validate(minimal_process(executesProtocol={}))


def test_load_investigation():
    # Should test the validation process of the ISA JSON file where root level = investigation.
    valid_investigation01 = load_investigation(METABOLIGHTS_ISA)
    assert len(valid_investigation01.studies) == 1
    assert valid_investigation01.studies[0].identifier == "BH2024"

    valid_investigation02 = load_investigation(ENA_MINIMAL_ISA)
    assert len(valid_investigation02.studies) == 1
    assert (
        valid_investigation02.studies[0].title
        == "Integrated multi-omics profiling of Arabidopsis thaliana under controlled experimental conditions"
    )

    with pytest.raises(ValidationError):
        load_investigation("./tests/fixtures/invalid_investigation.json")


def test_reduce_investigation_for_target_repo():
    good_investigation = _multi_repository_investigation()

    filtered_investigation = reduce_investigation_for_target_repo(
        good_investigation, TargetRepository.ENA.value
    )

    good_investigation_study = good_investigation.studies[0]

    filtered_investigation_study = filtered_investigation.studies[0]

    assert len(good_investigation_study.assays) == 2
    assert len(filtered_investigation_study.assays) == 1
    assert is_assay_for_target_repo(
        filtered_investigation_study.assays[0], TargetRepository.ENA.value
    )


def test_reduce_investigation_for_biosamples():
    good_investigation = _multi_repository_investigation()

    filtered_investigation = reduce_investigation_for_target_repo(
        good_investigation, TargetRepository.BIOSAMPLES.value
    )

    assert len(filtered_investigation.studies[0].assays) == 0


def _ids(nodes) -> list[str]:
    return [node.id for node in nodes]


def test_reduce_investigation_removes_unused_study_resources_for_ena():
    study = reduce_investigation_for_target_repo(
        load_investigation(HOLOFOOD_ISA), TargetRepository.ENA.value
    ).studies[0]

    assert _ids(study.assays) == ["#assay/262_263"]
    assert _ids(study.materials.samples) == ["#sample/3066"]
    assert _ids(study.materials.sources) == ["#source/3064"]
    # The sample collection protocol and the protocols of the ENA assay
    assert _ids(study.protocols) == [
        "#protocol/study_98_2908",
        "#protocol/assay_262_86",
        "#protocol/assay_263_2926",
    ]


def test_reduce_investigation_removes_unused_study_resources_for_metabolights():
    study = reduce_investigation_for_target_repo(
        load_investigation(HOLOFOOD_ISA), TargetRepository.METABOLIGHTS.value
    ).studies[0]

    assert _ids(study.materials.samples) == ["#sample/3065"]
    assert _ids(study.materials.sources) == ["#source/3064"]
    assert _ids(study.protocols) == [
        "#protocol/study_98_2908",
        "#protocol/assay_268_87",
        "#protocol/assay_269_88",
        "#protocol/assay_270_2972",
    ]


def test_reduce_investigation_keeps_all_study_resources_for_biosamples():
    investigation = load_investigation(HOLOFOOD_ISA)
    original = investigation.studies[0]

    study = reduce_investigation_for_target_repo(
        investigation, TargetRepository.BIOSAMPLES.value
    ).studies[0]

    assert study.assays == []
    assert study.materials == original.materials
    assert study.processSequence == original.processSequence
    assert study.protocols == original.protocols


def test_reduce_investigation_does_not_change_the_input_investigation():
    investigation = load_investigation(HOLOFOOD_ISA)
    original = investigation.model_copy(deep=True)

    reduce_investigation_for_target_repo(investigation, TargetRepository.ENA.value)

    assert investigation == original


def test_reduce_investigation_removes_sources_of_removed_samples():
    # Sample 3065 (MetaboLights) is collected from another source than 3066 (ENA)
    investigation = load_investigation(HOLOFOOD_ISA)
    study = investigation.studies[0]
    other_source = study.materials.sources[0].model_copy(
        update={"id": "#source/other", "name": "other source"}
    )
    study.materials.sources.append(other_source)
    collection_3065 = study.processSequence[0]
    assert _ids(collection_3065.outputs) == ["#sample/3065"]
    collection_3065.inputs = [IsaReference.model_validate({"@id": "#source/other"})]

    ena = reduce_investigation_for_target_repo(
        investigation, TargetRepository.ENA.value
    ).studies[0]
    metabolights = reduce_investigation_for_target_repo(
        investigation, TargetRepository.METABOLIGHTS.value
    ).studies[0]

    assert _ids(ena.materials.sources) == ["#source/3064"]
    assert _ids(metabolights.materials.sources) == ["#source/other"]


def test_data_type_is_free_text():
    # The type is not restricted to a fixed list of values
    assert Data.model_validate(
        {"@id": "data_001", "name": "data 1", "type": "Image File"}
    )
    custom = Data.model_validate(
        {"@id": "data_002", "name": "data 2", "type": "Custom File"}
    )
    assert custom.type == "Custom File"


def test_material_type_is_free_text():
    # The type is not restricted to a fixed list of values
    required = {"characteristics": [{"category": {"@id": "#cc/1"}}]}
    assert Material.model_validate(
        {
            "@id": "material_001",
            "name": "material 1",
            "type": "Extract Name",
            **required,
        }
    )
    custom = Material.model_validate(
        {
            "@id": "material_002",
            "name": "material 2",
            "type": "Custom Material",
            **required,
        }
    )
    assert custom.type == "Custom Material"


def test_target_repo_comment_validator():
    valid_assay_json = minimal_assay(
        comments=[
            {
                "@id": "comment_001",
                "name": f"{TARGET_REPO_KEY}",
                "value": TargetRepository.ENA.value,
            }
        ],
    )

    invalid_assay_json = minimal_assay(
        comments=[
            {
                "@id": "comment_002",
                "name": f"{TARGET_REPO_KEY}",
                "value": "my special repo",
            }
        ],
    )

    second_invalid_assay_json = minimal_assay(comments=[])

    third_invalid_assay_json = minimal_assay(
        comments=[
            {
                "@id": "comment_003",
                "name": f"{TARGET_REPO_KEY}",
                "value": TargetRepository.ENA.value,
            },
            {
                "@id": "comment_004",
                "name": f"{TARGET_REPO_KEY}",
                "value": TargetRepository.METABOLIGHTS.value,
            },
        ],
    )

    assert Assay.model_validate(valid_assay_json)
    with pytest.raises(
        ValidationError, match=f"Invalid '{TARGET_REPO_KEY}' value: 'my special repo'"
    ):
        Assay.model_validate(invalid_assay_json)

    with pytest.raises(
        ValidationError, match=f"'{TARGET_REPO_KEY}' comment is missing"
    ):
        Assay.model_validate(second_invalid_assay_json)

    with pytest.raises(
        ValidationError, match=f"Multiple '{TARGET_REPO_KEY}' comments found"
    ):
        Assay.model_validate(third_invalid_assay_json)

    def test_person_phone_nr_validator():
        valid_person_json = {
            "@id": "person_001",
            "phone_nr": "+49123456789",
        }

        invalid_person_json = {
            "@id": "person_002",
            "phone_nr": "123456789",
        }

        assert Person.model_validate(valid_person_json)

        with pytest.raises(ValidationError, match="Invalid number format"):
            Person.model_validate(invalid_person_json)


def test_update_study_materials_no_accession_categories():
    # This file has no characteristics for accessions
    json_path = "MARS/test-data/ena-minimal-no-accession-category.json"
    with open(json_path) as json_file:
        json_data = json.load(json_file)

    validated_investigation = Investigation.model_validate(json_data)

    response_file_path = "tests/fixtures/mars_receipts/biosamples_success_response.json"
    repo_response = RepositoryResponse.from_json_file(response_file_path)

    updated_investigation = update_investigation(validated_investigation, repo_response)

    # Check the accession number of the source
    source_accession = "SAMEA131504583"
    assert (
        updated_investigation.studies[0]
        .materials.sources[0]
        .characteristics[-1]
        .value.annotationValue
        == source_accession
    )

    # Check the accession number of the sample
    sample_accession = "SAMEA131504584"
    assert (
        updated_investigation.studies[0]
        .materials.samples[0]
        .characteristics[-1]
        .value.annotationValue
        == sample_accession
    )


def test_update_study_materials_with_accession_categories():
    # This file declares an accession characteristic category
    json_path = ENA_MINIMAL_ISA
    with open(json_path) as json_file:
        json_data = json.load(json_file)

    validated_investigation = Investigation.model_validate(json_data)

    response_file_path = "tests/fixtures/mars_receipts/biosamples_success_response.json"
    repo_response = RepositoryResponse.from_json_file(response_file_path)

    updated_investigation = update_investigation(validated_investigation, repo_response)
    study = updated_investigation.studies[0]
    accession_category_id = "#characteristic_category/accession_329"

    def accession_values(material):
        # Characteristics reference their category by '@id' only in this file.
        return [
            char.value.annotationValue
            for char in material.characteristics
            if char.category and char.category.id == accession_category_id
        ]

    # The source already has an accession characteristic: it is filled in,
    # not duplicated.
    assert accession_values(study.materials.sources[0]) == ["SAMEA131504583"]

    # The sample has none yet: exactly one is created.
    assert accession_values(study.materials.samples[0]) == ["SAMEA131504584"]


def test_update_study_materials_multiple_samples_from_one_source():
    json_path = ENA_COMPLETE_ISA
    with open(json_path) as json_file:
        json_data = json.load(json_file)

    study = json_data["studies"][0]
    root_sample = next(
        sample for sample in study["materials"]["samples"] if sample["name"] == "root 1"
    )
    root_sample["derivesFrom"][0]["@id"] = "#source/330"
    root_process = next(
        process
        for process in study["processSequence"]
        if process["@id"] == "#process/sample_collection/431"
    )
    root_process["inputs"][0]["@id"] = "#source/330"

    validated_investigation = Investigation.model_validate(json_data)
    repo_response = RepositoryResponse.model_validate(
        {
            "targetRepository": "biosamples",
            "errors": [],
            "info": [],
            "accessions": [
                {
                    "value": "SAMEA_SOURCE_1",
                    "path": [
                        {
                            "key": "studies",
                            "where": {
                                "key": "title",
                                "value": study["title"],
                            },
                        },
                        {"key": "materials"},
                        {
                            "key": "sources",
                            "where": {"key": "name", "value": "plant 1"},
                        },
                    ],
                },
                {
                    "value": "SAMEA_LEAF_1",
                    "path": [
                        {
                            "key": "studies",
                            "where": {
                                "key": "title",
                                "value": study["title"],
                            },
                        },
                        {"key": "materials"},
                        {
                            "key": "samples",
                            "where": {"key": "name", "value": "leaf 1"},
                        },
                    ],
                },
                {
                    "value": "SAMEA_ROOT_1",
                    "path": [
                        {
                            "key": "studies",
                            "where": {
                                "key": "title",
                                "value": study["title"],
                            },
                        },
                        {"key": "materials"},
                        {
                            "key": "samples",
                            "where": {"key": "name", "value": "root 1"},
                        },
                    ],
                },
            ],
        }
    )

    updated_investigation = update_investigation(validated_investigation, repo_response)
    updated_study = updated_investigation.studies[0]
    updated_samples = updated_study.materials.samples

    assert updated_study.materials.sources[0].characteristics[
        -1
    ].value.annotationValue == ("SAMEA_SOURCE_1")
    assert updated_samples[0].derivesFrom[0].id == "#source/330"
    assert (
        updated_samples[0].characteristics[-1].value.annotationValue == "SAMEA_LEAF_1"
    )
    assert updated_samples[1].derivesFrom[0].id == "#source/330"
    assert (
        updated_samples[1].characteristics[-1].value.annotationValue == "SAMEA_ROOT_1"
    )


def _biosamples_material_path(study_title, material_key, name):
    return [
        {"key": "studies", "where": {"key": "title", "value": study_title}},
        {"key": "materials"},
        {"key": material_key, "where": {"key": "name", "value": name}},
    ]


def _accession_characteristic(material, category_id):
    return next(
        (char, index)
        for index, char in enumerate(material.characteristics)
        if char.category and char.category.id == category_id
    )


def test_update_material_accession_keeps_existing_value_fields():
    investigation = load_investigation(ENA_COMPLETE_ISA)
    study = investigation.studies[0]
    accession_category_id = "#characteristic_category/accession_329"
    sources = {source.id: source for source in study.materials.sources}

    # plant 1 has an accession characteristic with empty termSource/termAccession;
    # plant 2's value additionally carries its own @id.
    _, plant_1_index = _accession_characteristic(
        sources["#source/330"], accession_category_id
    )
    plant_2_value, _ = _accession_characteristic(
        sources["#source/430"], accession_category_id
    )
    plant_2_value.value.id = "#ontology_annotation/existing_plant_2"

    repo_response = RepositoryResponse.model_validate(
        {
            "targetRepository": "biosamples",
            "errors": [],
            "info": [],
            "accessions": [
                {
                    "value": "SAMEA_PLANT_1",
                    "path": _biosamples_material_path(
                        study.title, "sources", "plant 1"
                    ),
                },
                {
                    "value": "SAMEA_PLANT_2",
                    "path": _biosamples_material_path(
                        study.title, "sources", "plant 2"
                    ),
                },
                {
                    "value": "SAMEA_LEAF_1",
                    "path": _biosamples_material_path(study.title, "samples", "leaf 1"),
                },
            ],
        }
    )

    updated_study = update_investigation(investigation, repo_response).studies[0]
    updated_sources = {source.id: source for source in updated_study.materials.sources}
    updated_samples = {sample.id: sample for sample in updated_study.materials.samples}

    # Existing value: filled in place, empty term fields kept, position unchanged.
    plant_1_char, index = _accession_characteristic(
        updated_sources["#source/330"], accession_category_id
    )
    assert index == plant_1_index
    assert plant_1_char.value.annotationValue == "SAMEA_PLANT_1"
    assert plant_1_char.value.termSource == ""
    assert plant_1_char.value.termAccession == ""
    assert plant_1_char.value.id == "#ontology_annotation/accession_source_330"

    # Existing value with its own @id keeps that id.
    plant_2_char, _ = _accession_characteristic(
        updated_sources["#source/430"], accession_category_id
    )
    assert plant_2_char.value.annotationValue == "SAMEA_PLANT_2"
    assert plant_2_char.value.id == "#ontology_annotation/existing_plant_2"

    # No accession characteristic yet: a new one gets a clean, single-'#' id.
    leaf_1_char, _ = _accession_characteristic(
        updated_samples["#sample/331"], accession_category_id
    )
    assert leaf_1_char.value.annotationValue == "SAMEA_LEAF_1"
    assert leaf_1_char.value.id == "#ontology_annotation/accession_sample_331"


ENA_STUDY_TITLE = "Arabidopsis thaliana"
ENA_ASSAY_ID = "#assay/18_20_21"


def _comment_values(comments, name):
    return [comment.value for comment in comments if comment.name == name]


def _ena_study_receipt(*accession_paths):
    """Builds an ENA MARS receipt with one study accession per (value, path) pair."""
    return RepositoryResponse.model_validate(
        {
            "targetRepository": "ena",
            "errors": [],
            "info": [],
            "accessions": [
                {"value": value, "path": path} for value, path in accession_paths
            ],
        }
    )


def _study_path(title=ENA_STUDY_TITLE):
    return [{"key": "studies", "where": {"key": "title", "value": title}}]


def _assay_path(assay_id, title=ENA_STUDY_TITLE):
    return _study_path(title) + [
        {"key": "assays", "where": {"key": "@id", "value": assay_id}}
    ]


def test_update_ena_study_accession_on_assay():
    # The ENA service creates one ENA study per assay, so the receipt points the
    # study accession at the assay.
    investigation = load_investigation(
        "tests/fixtures/isa_jsons/1_after_biosamples.json"
    )
    ena_response = RepositoryResponse.from_json_file(
        "tests/fixtures/mars_receipts/ena_success_response.json"
    )

    updated_investigation = update_investigation(investigation, ena_response)
    study = updated_investigation.studies[0]
    ena_assay = next(
        assay for assay in study.assays if is_assay_for_target_repo(assay, "ena")
    )

    assert _comment_values(ena_assay.comments, "ena_study_accession") == ["ERP208805"]
    assert _comment_values(ena_assay.comments, "ena_assay_accession") == []
    assert _comment_values(study.comments, "ena_study_accession") == []


def test_update_ena_study_accession_on_study():
    # A receipt pointing at the study itself still annotates the study.
    investigation = load_investigation(
        "tests/fixtures/isa_jsons/1_after_biosamples.json"
    )
    ena_response = _ena_study_receipt(("ERP000001", _study_path()))

    updated_investigation = update_investigation(investigation, ena_response)
    study = updated_investigation.studies[0]

    assert _comment_values(study.comments, "ena_study_accession") == ["ERP000001"]
    for assay in study.assays:
        assert _comment_values(assay.comments, "ena_study_accession") == []


def test_update_ena_study_accessions_for_multiple_assays():
    # Each ENA assay becomes its own ENA study; every accession must land on
    # the assay the receipt points to, not on the first ENA assay of the study.
    investigation = load_investigation(
        "tests/fixtures/isa_jsons/1_after_biosamples.json"
    )
    study = investigation.studies[0]
    second_assay = study.assays[0].model_copy(deep=True)
    second_assay.id = "#assay/second_ena_assay"
    study.assays.append(second_assay)

    ena_response = _ena_study_receipt(
        ("ERP000001", _assay_path(ENA_ASSAY_ID)),
        ("ERP000002", _assay_path("#assay/second_ena_assay")),
    )

    updated_investigation = update_investigation(investigation, ena_response)
    first, second = updated_investigation.studies[0].assays

    assert _comment_values(first.comments, "ena_study_accession") == ["ERP000001"]
    assert _comment_values(second.comments, "ena_study_accession") == ["ERP000002"]


def test_update_ena_study_accession_unknown_assay():
    investigation = load_investigation(
        "tests/fixtures/isa_jsons/1_after_biosamples.json"
    )
    ena_response = _ena_study_receipt(
        ("ERP000001", _assay_path("#assay/does_not_exist"))
    )

    with pytest.raises(ValueError, match="Node not found for ERP000001"):
        update_investigation(investigation, ena_response)


def test_update_experiment_accession_on_library():
    investigation = load_investigation(
        "tests/fixtures/isa_jsons/1_after_biosamples.json"
    )
    ena_response = RepositoryResponse.from_json_file(
        "tests/fixtures/mars_receipts/ena_success_response.json"
    )

    updated_investigation = update_investigation(investigation, ena_response)
    other_materials = {
        material.id: material
        for material in updated_investigation.studies[0]
        .assays[0]
        .materials.otherMaterials
    }

    library_accessions = [
        char.value.annotationValue
        for char in other_materials["#other_material/333"].characteristics
        if char.category
        and char.category.characteristicType
        and char.category.characteristicType.annotationValue == "accession"
    ]
    assert library_accessions == ["ERX17495163"]


def test_update_datafile_comment_with_accession_comment_present():
    json_path = "tests/fixtures/isa_jsons/1_after_biosamples.json"
    investigation = load_investigation(json_path)
    response_file_path = "tests/fixtures/mars_receipts/ena_success_response.json"
    ena_response = RepositoryResponse.from_json_file(response_file_path)
    data_file_accession_number = "ERR18102735"

    updated_investigation = update_investigation(investigation, ena_response)
    data_file_comments = (
        updated_investigation.studies[0].assays[0].dataFiles[0].comments
    )
    accession_comment = filter(lambda x: x.name == "accession", data_file_comments)
    assert next(accession_comment).value == data_file_accession_number


def test_filename_validation():
    # ISA should have a filename that starts with 'x_'
    with pytest.raises(ValidationError, match="'filename' should start with 'i_'"):
        Investigation.model_validate(minimal_investigation(filename="bad filename"))

    with pytest.raises(ValidationError, match="'filename' should start with 's_'"):
        Study.model_validate(minimal_study(filename="bad filename"))

    with pytest.raises(ValidationError, match="'filename' should start with 'a_'"):
        Assay.model_validate(minimal_assay(filename="bad filename"))

    assert re.match(r"^i_", "i_Good_file_name")

    assert Investigation.model_validate(
        minimal_investigation(filename="i_Good_File_Name")
    )
    assert Study.model_validate(minimal_study(filename="s_Good_File_Name"))
    assert Assay.model_validate(minimal_assay(filename="a_Good_File_Name"))


@pytest.mark.parametrize(
    "filename, prefix, arc_kind, expected",
    [
        # ISA-Tab style names
        ("i_investigation.txt", "i", "investigation", True),
        ("s_study.txt", "s", "study", True),
        ("a_assay.txt", "a", "assay", True),
        ("a_", "a", "assay", True),
        # ARC layout
        ("isa.investigation.xlsx", "i", "investigation", True),
        ("studies/MyStudy/isa.study.xlsx", "s", "study", True),
        ("assays/MyAssay/isa.assay.xlsx", "a", "assay", True),
        # ARC file for a different ISA level
        ("isa.assay.xlsx", "s", "study", False),
        ("isa.study.xlsx", "a", "assay", False),
        ("isa.investigation.xlsx", "s", "study", False),
        ("studies/MyStudy/isa.study.xlsx", "a", "assay", False),
        ("assays/MyAssay/isa.assay.xlsx", "s", "study", False),
        # ARC file names outside the ARC folder layout
        ("isa.study.xlsx", "s", "study", False),
        ("isa.assay.xlsx", "a", "assay", False),
        ("studies/isa.study.xlsx", "s", "study", False),
        ("studies/MyStudy/Sub/isa.study.xlsx", "s", "study", False),
        ("studies//isa.study.xlsx", "s", "study", False),
        ("/etc/isa.assay.xlsx", "a", "assay", False),
        ("../../isa.assay.xlsx", "a", "assay", False),
        ("../assays/MyAssay/isa.assay.xlsx", "a", "assay", False),
        ("/assays/MyAssay/isa.assay.xlsx", "a", "assay", False),
        (r"C:\x\isa.assay.xlsx", "a", "assay", False),
        (r"assays\MyAssay\isa.assay.xlsx", "a", "assay", False),
        ("studies/Foo/isa.assay.xlsx", "a", "assay", False),
        ("assays/MyAssay/isa.assay.xlsx/notes.txt", "a", "assay", False),
        ("isa.assay.xlsx/other.txt", "a", "assay", False),
        ("assays/MyAssay/isa.assay.xlsx\n", "a", "assay", False),
        # Near misses
        ("isa.assay.xlsx.bak", "a", "assay", False),
        ("xisa.assay.xlsx", "a", "assay", False),
        ("isa.assay.xls", "a", "assay", False),
        ("ISA.ASSAY.XLSX", "a", "assay", False),
        ("assay.txt", "a", "assay", False),
        ("A_assay.txt", "a", "assay", False),
        (" a_assay.txt", "a", "assay", False),
        ("", "a", "assay", False),
        # The ISA-Tab prefix is only checked at the start of the whole name
        ("studies/a_assay.txt", "a", "assay", False),
    ],
)
def test_is_valid_filename(filename, prefix, arc_kind, expected):
    assert _is_valid_filename(filename, prefix, arc_kind) is expected


@pytest.mark.parametrize(
    "model, filename",
    [
        (Investigation, "isa.investigation.xlsx"),
        (Investigation, "i_investigation.txt"),
        (Study, "studies/MyStudy/isa.study.xlsx"),
        (Study, "s_study.txt"),
        (Assay, "assays/MyAssay/isa.assay.xlsx"),
        (Assay, "a_assay.txt"),
    ],
)
def test_filename_validation_accepts_valid_names(model, filename):
    parsed = model.model_validate(minimal(model, filename=filename))
    assert parsed.filename == filename


FILENAME_ERRORS = {
    Investigation: "'filename' should start with 'i_' or be 'isa.investigation.xlsx'",
    Study: "'filename' should start with 's_' or be 'studies/<name>/isa.study.xlsx'",
    Assay: "'filename' should start with 'a_' or be 'assays/<name>/isa.assay.xlsx'",
}


@pytest.mark.parametrize(
    "model, filename",
    [
        (Investigation, "isa.study.xlsx"),
        (Investigation, "isa.assay.xlsx"),
        (Study, "isa.investigation.xlsx"),
        (Study, "isa.assay.xlsx"),
        (Assay, "isa.investigation.xlsx"),
        (Assay, "isa.study.xlsx"),
        (Assay, "isa.assay.xlsx.bak"),
        (Assay, "assays/MyAssay/isa.assay.xlsx.bak"),
        (Study, "studies/isa.study.xlsx/notes.txt"),
        # Right file name, wrong folder layout
        (Study, "isa.study.xlsx"),
        (Assay, "isa.assay.xlsx"),
        (Assay, "/etc/isa.assay.xlsx"),
        (Assay, "../../isa.assay.xlsx"),
        (Assay, r"C:\x\isa.assay.xlsx"),
        (Assay, r"assays\MyAssay\isa.assay.xlsx"),
        (Assay, "studies/Foo/isa.assay.xlsx"),
    ],
)
def test_filename_validation_rejects_invalid_names(model, filename):
    with pytest.raises(ValidationError) as error:
        model.model_validate(minimal(model, filename=filename))
    # The message names both accepted forms
    assert FILENAME_ERRORS[model] in str(error.value)


@pytest.mark.parametrize("model", [Investigation, Study, Assay])
def test_filename_validation_requires_filename(model):
    document = minimal(model)
    del document["filename"]
    with pytest.raises(ValidationError, match="filename\n  Field required"):
        model.model_validate(document)
    with pytest.raises(ValidationError, match="filename"):
        model.model_validate({**document, "filename": None})


@pytest.mark.skip(
    reason="ARC ISA-JSON currently does not comply to the MARS specifications and needs upstream fixes."
)
def test_load_arc_example():
    investigation = load_investigation("MARS/test-data/ARC-ISA-example.json")
    assert isinstance(investigation, Investigation)
    assert investigation.filename == "isa.investigation.xlsx"
    assert len(investigation.studies) == 2
    for study in investigation.studies:
        assert re.fullmatch(r"studies/[^/]+/isa\.study\.xlsx", study.filename)
        for assay in study.assays:
            assert re.fullmatch(r"assays/[^/]+/isa\.assay\.xlsx", assay.filename)


def test_map_data_files_to_repositories():
    investigation = _multi_repository_investigation()
    # Only the file names are matched against the ISA-JSON; the files need not exist.
    metabolights_files = [
        "data/metpro-analysis.txt",
        "data/ms-data-metpro--1.mzml",
        "data/ms-data-metpro--2.mzml",
        "data/ms-data-metpro--3.mzml",
        "data/ms-data-metpro--4.mzml",
    ]
    ena_files = [
        "data/ENA_TEST2.EXP1.R1.fastq.gz",
        "data/ENA_TEST2.EXP1.R2.fastq.gz",
        "data/ENA_TEST2.EXP2.SE.fastq.gz",
    ]
    exact_match_files = metabolights_files + ena_files

    check_map = {
        TargetRepository.METABOLIGHTS.value: metabolights_files,
        TargetRepository.ENA.value: ena_files,
    }
    assert check_map == map_data_files_to_repositories(exact_match_files, investigation)

    not_enough_files = exact_match_files[:-1]

    with pytest.raises(
        ValueError,
        match=rf"Assay for repository '{TargetRepository.ENA.value}' has encountered",
    ):
        map_data_files_to_repositories(not_enough_files, investigation)

    too_many_files = exact_match_files.copy()
    one_too_many = "data/one-too-many.fastq"
    too_many_files.append(one_too_many)

    result_maps = map_data_files_to_repositories(too_many_files, investigation)

    assert one_too_many not in [
        value for key, value_list in result_maps.items() for value in value_list
    ]

    duplicated_files = exact_match_files.copy()
    duplicated_files.append(exact_match_files[-1])

    map_data_files_to_repositories(duplicated_files, investigation)


# Test data that does not comply with the strict MARS model (yet). strict=True:
# once a file complies, its test fails so the entry gets removed.
NON_COMPLIANT_TEST_FILES = {
    "MARS/test-data/ARC-ISA-example.json": (
        "ARC ISA-JSON currently does not comply to the MARS specifications "
        "and needs upstream fixes."
    ),
}


def _isa_json_test_files():
    """Every ISA JSON in MARS/test-data, so new files are picked up automatically,
    plus the fixture the tests load as an Investigation."""
    files = sorted(
        path.as_posix()
        for path in Path("MARS/test-data").rglob("*.json")
        if "receipt" not in path.name
    )
    files.append("tests/fixtures/isa_jsons/1_after_biosamples.json")
    return [
        (
            pytest.param(
                file,
                marks=pytest.mark.xfail(
                    reason=NON_COMPLIANT_TEST_FILES[file], strict=True
                ),
            )
            if file in NON_COMPLIANT_TEST_FILES
            else file
        )
        for file in files
    ]


# ISA JSON files that the tests (and thus the CI workflow) load as an Investigation.
# They all have to comply with the strict MARS model.
ISA_JSON_TEST_FILES = _isa_json_test_files()


# The MARS receipts the repository services write; ena-receipt*.json are raw ENA
# receipts, not MARS receipts.
@pytest.mark.parametrize(
    "file_path",
    sorted(
        path.as_posix() for path in Path("MARS/test-data").glob("mars-*receipt*.json")
    ),
)
def test_mars_receipt_parses(file_path):
    RepositoryResponse.from_json_file(file_path)


@pytest.mark.parametrize("file_path", ISA_JSON_TEST_FILES)
def test_test_data_complies_with_model(file_path):
    with open(file_path, "r") as json_file:
        assert Investigation.model_validate(json.load(json_file))


def _contains_none(obj) -> bool:
    if obj is None:
        return True
    if isinstance(obj, dict):
        return any(_contains_none(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_none(v) for v in obj)
    return False


@pytest.mark.parametrize("file_path", ISA_JSON_TEST_FILES)
def test_serialization_with_exclude_none_drops_all_none_values(file_path):
    investigation = load_investigation(file_path)

    as_dict = investigation.model_dump(by_alias=True, exclude_none=True)
    as_json = json.loads(investigation.model_dump_json(by_alias=True, exclude_none=True))

    assert not _contains_none(as_dict)
    assert not _contains_none(as_json)
    # Dropping the None values must not lose information
    assert Investigation.model_validate(as_dict) == investigation


def test_serialization_drops_optional_fields_that_are_none():
    attribute = MaterialAttribute.model_validate({"@id": "#attr/1"})
    value = MaterialAttributeValue.model_validate(
        {"@id": None, "category": {"@id": "#attr/1"}, "value": None, "unit": None}
    )

    # Without the flag the optional fields are serialized as null
    assert value.model_dump(by_alias=True)["@id"] is None
    assert value.model_dump(by_alias=True)["category"]["characteristicType"] is None

    dumped = value.model_dump(by_alias=True, exclude_none=True)
    assert dumped["category"] == attribute.model_dump(by_alias=True, exclude_none=True)
    for key in ("@id", "value", "unit"):
        assert key not in dumped
    assert "characteristicType" not in dumped["category"]
    assert not _contains_none(dumped)


def test_serialization_drops_values_set_to_none_after_validation():
    value = MaterialAttributeValue.model_validate(
        {
            "@id": "#value/1",
            "category": {"@id": "#attr/1"},
            "value": {"@id": "#ann/1", "annotationValue": "x"},
            "unit": {"@id": "#unit/1"},
        }
    )
    value.id = None
    value.unit = None
    value.value = OntologyAnnotation.model_validate({"annotationValue": None})

    dumped = value.model_dump(by_alias=True, exclude_none=True)

    assert not _contains_none(dumped)
    assert "@id" not in dumped
    assert "unit" not in dumped
    assert "annotationValue" not in dumped["value"]


def test_invalid_test_data_is_rejected():
    with open("tests/fixtures/invalid_investigation.json", "r") as json_file:
        with pytest.raises(ValidationError):
            Investigation.model_validate(json.load(json_file))


def test_assay_requires_target_repository_comment_even_without_comments_key():
    # 'comments' is absent (not just empty): the mandatory 'target_repository'
    # comment must still be enforced.
    assay = minimal_assay()
    del assay["comments"]
    with pytest.raises(
        ValidationError, match=f"'{TARGET_REPO_KEY}' comment is missing"
    ):
        Assay.model_validate(assay)


def test_ontology_source_reference_and_publication_accept_an_id():
    assert OntologySourceReference.model_validate({"@id": "#osr/1", "name": "EFO"}).id
    assert Publication.model_validate({"@id": "#publication/1", "title": "t"}).id
    # the '@id' of nested annotations was already supported
    assert Publication.model_validate({"status": {"@id": "#status/1"}})
    assert Person.model_validate({"roles": [{"@id": "#role/1"}]})
