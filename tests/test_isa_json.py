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
    Study,
    _is_valid_filename,
)
from mars_lib.models.repository_response import RepositoryResponse
import json


METABOLIGHTS_ISA = "MARS/test-data/metabolights-input-isa.json"
ENA_COMPLETE_ISA = "MARS/test-data/ena-complete.json"


def _multi_repository_investigation():
    """The MetaboLights LC-MS study with the ENA sequencing assay added, so one
    investigation holds assays for two target repositories."""
    investigation = load_investigation(METABOLIGHTS_ISA)
    ena_assay = load_investigation(ENA_COMPLETE_ISA).studies[0].assays[0]
    investigation.studies[0].assays.append(ena_assay)
    return investigation


def test_load_investigation():
    # Should test the validation process of the ISA JSON file where root level = investigation.
    valid_investigation01 = load_investigation(METABOLIGHTS_ISA)
    assert len(valid_investigation01.studies) == 1
    assert valid_investigation01.studies[0].identifier == "BH2024"

    valid_investigation02 = load_investigation(
        "MARS/test-data/biosamples-input-isa.json"
    )
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
    assert Material.model_validate(
        {"@id": "material_001", "name": "material 1", "type": "Extract Name"}
    )
    custom = Material.model_validate(
        {"@id": "material_002", "name": "material 2", "type": "Custom Material"}
    )
    assert custom.type == "Custom Material"


def test_target_repo_comment_validator():
    valid_assay_json = {
        "@id": "assay_001",
        "comments": [
            {
                "@id": "comment_001",
                "name": f"{TARGET_REPO_KEY}",
                "value": TargetRepository.ENA.value,
            }
        ],
    }

    invalid_assay_json = {
        "@id": "assay_002",
        "comments": [
            {
                "@id": "comment_002",
                "name": f"{TARGET_REPO_KEY}",
                "value": "my special repo",
            }
        ],
    }

    second_invalid_assay_json = {"@id": "assay_003", "comments": []}

    third_invalid_assay_json = {
        "@id": "assay_004",
        "comments": [
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
    }

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
    json_path = "MARS/test-data/biosamples-original-isa-no-accesion-char.json"
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
    # This file has no characteristics for accessions
    json_path = "MARS/test-data/biosamples-original-isa.json"
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
        Investigation.model_validate({"@id": "1", "filename": "bad filename"})

    with pytest.raises(ValidationError, match="'filename' should start with 's_'"):
        Study.model_validate({"@id": "2", "filename": "bad filename"})

    with pytest.raises(ValidationError, match="'filename' should start with 'a_'"):
        Assay.model_validate({"@id": "3", "filename": "bad filename"})

    assert re.match(r"^i_", "i_Good_file_name")

    assert Investigation.model_validate({"@id": "4", "filename": "i_Good_File_Name"})
    assert Study.model_validate({"@id": "5", "filename": "s_Good_File_Name"})
    assert Assay.model_validate({"@id": "6", "filename": "a_Good_File_Name"})


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
    parsed = model.model_validate({"@id": "1", "filename": filename})
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
        model.model_validate({"@id": "1", "filename": filename})
    # The message names both accepted forms
    assert FILENAME_ERRORS[model] in str(error.value)


@pytest.mark.parametrize("model", [Investigation, Study, Assay])
def test_filename_validation_allows_missing_filename(model):
    assert model.model_validate({"@id": "1"}).filename is None
    assert model.model_validate({"@id": "1", "filename": None}).filename is None


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


TEST_ISA_JSON = "MARS/test-data/ena-complete.json"


def _isa_data_file_names(investigation) -> set[str]:
    return {
        df.name
        for study in investigation.studies
        for assay in study.assays
        for df in assay.dataFiles
    }


def test_check_data_files_all_matched():
    investigation = load_investigation(TEST_ISA_JSON)
    files = [f"data/{name}" for name in sorted(_isa_data_file_names(investigation))]

    report = check_data_files(files, investigation)

    assert report.matched == files
    assert report.unreferenced == []
    assert report.missing == []


def test_check_data_files_reports_unreferenced_and_missing():
    investigation = load_investigation(TEST_ISA_JSON)
    names = sorted(_isa_data_file_names(investigation))
    absent = names[0]
    present = names[1:]
    files = [f"data/{name}" for name in present] + ["data/not-in-isa.fastq"]

    report = check_data_files(files, investigation)

    assert len(report.matched) == len(present)
    assert report.unreferenced == ["data/not-in-isa.fastq"]
    assert report.missing == [absent]


def test_check_data_files_without_files_reports_all_missing():
    investigation = load_investigation(TEST_ISA_JSON)

    report = check_data_files([], investigation)

    assert report.matched == []
    assert report.unreferenced == []
    assert report.missing == sorted(_isa_data_file_names(investigation))
