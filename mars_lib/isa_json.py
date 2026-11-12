import json
import os
from dataclasses import dataclass, field
from typing import Any

from mars_lib.logging import print_and_log
from mars_lib.models.isa_json import (
    Investigation,
    Assay,
    Comment,
    MaterialAttribute,
    MaterialAttributeValue,
    Process,
    Study,
    OntologyAnnotation,
    Data as DataFile,
)
from pydantic import ValidationError
from mars_lib.target_repo import TARGET_REPO_KEY, TargetRepository
import uuid
from mars_lib.models.repository_response import (
    RepositoryResponse,
    Filter,
    Accession,
    Path,
)


def reduce_investigation_for_target_repo(
    input_investigation: Investigation, target_repo: str
) -> Investigation:
    """
    Filters out assays that are not meant to be sent to the specified target repository.

    BioSamples receives every study without its assays (and with all its samples).
    For the other repositories, a study without any assays is kept as is. In the other
    studies the resources that the remaining assays don't use are filtered out as well
    (see `remove_unused_study_resources`).

    Args:
        input_investigation (Investigation): Input ISA JSON that contains the original information.
        target_repo (TargetRepository): Target repository as a constant.

    Returns:
        Investigation: Filtered ISA JSON.

    Raises:
        ValueError: A study has assays, but none for the target repository, so there is
            nothing to submit to it.
    """
    filtered_investigation = input_investigation.model_copy(deep=True)
    for study in filtered_investigation.studies:
        if target_repo == TargetRepository.BIOSAMPLES.value:
            study.assays = []
        elif study.assays:
            study.assays = [
                assay
                for assay in study.assays
                if is_assay_for_target_repo(assay, target_repo)
            ]
            if not study.assays:
                raise ValueError(
                    f"Study '{study.title}' has no assays for the target repository "
                    f"'{target_repo}', so there is nothing to submit to it."
                )
            remove_unused_study_resources(study)

    return filtered_investigation


def remove_unused_study_resources(study: Study) -> None:
    """
    Removes the study level resources that the study's (filtered) assays don't use,
    in place. The assays' process sequences link the resources together: starting
    from the inputs of the assays' processes, the study process sequence is walked back
    (see `_upstream_study_processes`). Removed are:
    - the study processes that don't lead to an input of the assays;
    - the samples, other materials and sources that are neither an input of the assays,
      nor an input of a remaining study process;
    - the outputs of the remaining study processes that refer to a removed sample,
      other material or source;
    - the protocols that are not executed by a process in an assay, or by a remaining
      study process.

    Args:
        study (Study): The study, of which the assays are already filtered.
    """
    assay_processes = [
        process for assay in study.assays for process in assay.processSequence
    ]
    assay_input_ids = {
        node.id for process in assay_processes for node in process.inputs
    }
    study_processes, used_ids = _upstream_study_processes(study, assay_input_ids)

    # Filtering out the unused materials
    materials = study.materials
    material_ids = {
        material.id
        for material in materials.sources + materials.samples + materials.otherMaterials
    }
    materials.sources = [m for m in materials.sources if m.id in used_ids]
    materials.samples = [m for m in materials.samples if m.id in used_ids]
    materials.otherMaterials = [m for m in materials.otherMaterials if m.id in used_ids]
    removed_material_ids = material_ids - used_ids

    # A process can also produce materials that are removed (e.g. one collection process
    # for the samples of several repositories). Other outputs, like data files, stay.
    for process in study_processes:
        process.outputs = [  # type: ignore[assignment]
            output
            for output in process.outputs
            if output.id not in removed_material_ids
        ]
    study.processSequence = study_processes

    # Filtering the study protocols
    protocol_ids = {
        process.executesProtocol.id for process in assay_processes + study_processes
    }
    study.protocols = [
        protocol for protocol in study.protocols if protocol.id in protocol_ids
    ]


def _upstream_study_processes(
    study: Study, material_ids: set[str]
) -> tuple[list[Process], set[str]]:
    """
    Walks the study process sequence back from the given materials: keeps the processes
    that produce one of them, and the materials those processes take as input in turn,
    so a chain of several steps (e.g. source -> sample -> sample) is kept as a whole.

    A step can also be split over linked processes, e.g. one with only the source as
    input and one with only the sample as output. So for a kept process without inputs,
    the process it links back to via 'previousProcess' is kept as well.

    Args:
        study (Study): The study that holds the processes.
        material_ids (set[str]): The ids of the materials to start from.

    Returns:
        tuple[list[Process], set[str]]: The kept study processes (in their original
            order), and the ids of the given materials plus the inputs of those
            processes.
    """
    used_ids = set(material_ids)
    kept_process_ids: set[str] = set()
    linked_process_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for process in study.processSequence:
            if process.id in kept_process_ids:
                continue
            if process.id in linked_process_ids or any(
                output.id in used_ids for output in process.outputs
            ):
                kept_process_ids.add(process.id)
                used_ids |= {node.id for node in process.inputs}
                if not process.inputs and process.previousProcess is not None:
                    linked_process_ids.add(process.previousProcess.id)
                changed = True

    kept_processes = [
        process for process in study.processSequence if process.id in kept_process_ids
    ]
    return kept_processes, used_ids


def detect_target_repo_comment(comments: list[Comment]) -> Comment:
    """Will detect the comment that contains the target repository.

    Args:
        comments (list[Comment]): List of comments.

    Returns:
        Comment: The comment where the name corresponds with the name of the provided target repo.
    """
    if len(comments) < 1:
        raise ValueError("No comments found! Not able to detect the target repository!")
    return next(comment for comment in comments if comment.name == TARGET_REPO_KEY)


def is_assay_for_target_repo(assay: Assay, target_repo: str) -> bool:
    """
    Defines whether the assays are meant for the target repository.

    Args:
        assay (Assay): Assay model.
        target_repo (TargetRepository): Target repository as a constant.

    Returns:
        bool: Boolean defining whether the assay is destined for the provided target repo.
    """
    target_repo_comment = detect_target_repo_comment(assay.comments)
    if target_repo_comment.value == target_repo:
        return True
    else:
        return False


def load_investigation(file_path: str) -> Investigation | ValidationError:
    """
    Reads the file and validates it as a valid ISA JSON.

    The root level of the ISA JSON is the investigation.

    Args:
        file_path (str): Path to ISA JSON as string.

    Returns:
        Investigation | ValidationError: Depending on the validation, returns the investigation or a pydantic validation error.
    """
    with open(file_path, "r") as json_file:
        data = json.load(json_file)

    return Investigation.model_validate(data)


def get_filter_for_accession_key(accession: Accession, key: str) -> Filter | None:
    """
    Returns the studies node from the accession.

    Args:
        accession (Accession): The accession to be searched.
        key (str): The key to be searched.

    Returns:
        Path: The studies node.
    """
    return next((p.where for p in accession.path if p.key == key), None)


def apply_filter(filter: Filter, nodes: list[Study] | list[Assay]) -> Any:
    """
    Filters the studies based on the filter.

    Args:
        filter (Filter): The filter to be applied.
        studies (list[Study]): The studies to be filtered.

    Returns:
       Study: The filtered study.
    """
    filter_key = "id" if filter.key == "@id" else filter.key
    return next(
        (node for node in nodes if getattr(node, filter_key) == filter.value), None
    )


def accession_data_file_comment_present(node: DataFile) -> bool:
    accession_data_file_comments = [
        comment for comment in node.comments if comment.name == "accession"
    ]
    if len(accession_data_file_comments) > 1:
        raise AttributeError(
            "There should not be more than 1 data file comment with the name 'accession'."
        )
    elif len(accession_data_file_comments) > 0:
        return True
    else:
        return False


def accession_characteristic_category_present(node: Study | Assay) -> bool:
    """
    Checks if the node has an accession characteristic category.

    Args:
        node (Study | Assay): The study or assay to be checked.

    Returns:
        bool: Boolean indicating whether the node has an accession characteristic category.
    """
    accession_characteristics_categories = [
        char_cat
        for char_cat in node.characteristicCategories
        if char_cat.characteristicType
        and char_cat.characteristicType.annotationValue == "accession"
    ]

    if len(accession_characteristics_categories) > 1:
        raise AttributeError(
            "There should be not more than one accession characteristic category."
        )
    elif len(accession_characteristics_categories) > 0:
        return True
    else:
        return False


def accession_characteristic_present(
    node: Study | Assay, material_type_path: Path
) -> bool:
    """
    Checks if the node has an accession characteristic.

    Args:
        node (Study | Assay): The study or assay to be checked.
        material_type_path (Path): The path to the material type.

    Returns:
        bool: Boolean indicating whether the node has an accession characteristic.
    """
    if material_type_path.where:
        material = apply_filter(
            material_type_path.where, getattr(node.materials, material_type_path.key)
        )
    else:
        raise ValueError(
            f"'where' atribute is missing in path {material_type_path.key}."
        )

    # A characteristic usually references its category by '@id' only, so resolve
    # it against the node's categories instead of relying on an inline type.
    accession_category_ids = {
        char_cat.id
        for char_cat in node.characteristicCategories
        if char_cat.id
        and char_cat.characteristicType
        and char_cat.characteristicType.annotationValue == "accession"
    }

    accession_characteristics = [
        char
        for char in material.characteristics
        if char.category
        and (
            char.category.id in accession_category_ids
            or (
                char.category.characteristicType is not None
                and char.category.characteristicType.annotationValue == "accession"
            )
        )
    ]

    if len(accession_characteristics) > 1:
        raise AttributeError(
            "There should be not more than one accession characteristic."
        )
    elif len(accession_characteristics) > 0:
        return True
    else:
        return False


def add_accession_to_material_node(
    node: Any, accession_number: str, material_type_path: Path
) -> None:
    """
    Adds the accession number to the node.

    Args:
        node (Any): The node to be updated.
        accession_number (str): The accession number to be added.
        material_type_path (Path): The path to the material type.
    """
    if type(node) not in [Study, Assay]:
        raise ValueError("Node must be either 'Study' or 'Assay'.")

    node_materials = getattr(node.materials, material_type_path.key)
    if material_type_path.where:
        updated_material = apply_filter(material_type_path.where, node_materials)
    else:
        raise ValueError(
            f"'where' attribute is missing in path {material_type_path.key}."
        )

    accession_characteristics_category = next(
        (
            char_cat
            for char_cat in node.characteristicCategories
            if char_cat.characteristicType
            and char_cat.characteristicType.annotationValue == "accession"
        ),
        None,
    )

    if not accession_characteristics_category:
        raise ValueError("Accession characteristic category is not present.")

    updated_material_accession_characteristic = next(
        (
            char
            for char in updated_material.characteristics
            if char.category
            and char.category.id == accession_characteristics_category.id
        ),
        None,
    )

    if not updated_material_accession_characteristic:
        raise ValueError("Accession characteristic is not present.")

    # Fill in the existing value so its id, termSource, termAccession and
    # comments survive; only create a new annotation when there is none.
    accession_value = updated_material_accession_characteristic.value
    if not isinstance(accession_value, OntologyAnnotation):
        accession_value = OntologyAnnotation()
        updated_material_accession_characteristic.value = accession_value

    accession_value.annotationValue = accession_number
    if not accession_value.id:
        accession_value.id = accession_annotation_id(updated_material.id)


def accession_annotation_id(material_id: str | None) -> str:
    """
    Builds the '@id' for a material's accession annotation.

    Material ids already start with '#' (e.g. '#source/330'), so the material id
    is flattened ('source_330') to keep a single '#' in the resulting id.
    """
    suffix = (material_id or str(uuid.uuid4())).lstrip("#").replace("/", "_")
    return f"#ontology_annotation/accession_{suffix}"


def add_accession_to_data_file_node(node: DataFile, accession_number: str) -> None:
    accession_comment = next(
        (
            comment
            for comment in node.comments
            if comment.name and comment.name.lower() == "accession"
        ),
        None,
    )
    if not accession_comment:
        raise ValueError(
            f"Accession comment not found in data file'{node.id} - {node.name}'."
        )

    accession_comment.value = accession_number


def create_accession_characteristic_category(
    node: Study | Assay,
) -> tuple[str, MaterialAttribute]:
    """
    creates a new characteristic category for the accession number.

    Args:
        node (Study | Assay): node to be updated

    Returns:
        MaterialAttribute: The newly created characteristic category.
    """
    if type(node) not in [Study, Assay]:
        raise ValueError("Node must be either 'Study' or 'Assay'.")

    accession_id = str(uuid.uuid4())
    category = MaterialAttribute(
        **{"@id": f"#characteristic_category/accession_{accession_id}"},
        characteristicType=OntologyAnnotation(annotationValue="accession"),
    )
    node.characteristicCategories.append(category)

    return accession_id, category


def create_accession_data_file_comment(node: DataFile) -> Comment:
    comment = Comment()
    accession_id = str(uuid.uuid4())
    comment.id = f"#comment/accession_{accession_id}"
    comment.name = "accession"
    node.comments.append(comment)
    return comment


def fetch_existing_accession_data_file_comment(node: DataFile) -> Comment:
    accession_comment = next(
        comment
        for comment in node.comments
        if comment.name and comment.name.lower() == "accession"
    )
    if not accession_comment:
        raise ValueError(f"Accession comment not found in {node.id}.")
    return accession_comment


def fetch_existing_characteristic_category(
    node: Study | Assay,
) -> tuple[str, MaterialAttribute]:
    """
    Fetches the existing characteristic category for the accession number.

    Args:
        node (Study | Assay): study or assay to search
    """
    accession_cat = next(
        char_cat
        for char_cat in node.characteristicCategories
        if char_cat.characteristicType
        and char_cat.characteristicType.annotationValue
        and isinstance(char_cat.characteristicType.annotationValue, str)
        and char_cat.characteristicType.annotationValue.lower() == "accession"
    )
    if not accession_cat:
        raise ValueError(f"Accession characteristic category not found in{node.id}.")

    accession_id = (
        accession_cat.id.split("_")[-1] if accession_cat.id else str(uuid.uuid4())
    )
    return (accession_id, accession_cat)


def create_accession_characteristic(
    node: Study | Assay,
    material_type_path: Path,
    category: MaterialAttribute,
    accession_id: str,
) -> None:
    """
    Creates a new characteristic for the accession number.

    Args:
        node (Study | Assay): node to be updated
        material_type_path (Path): path to the material type,
        category (MaterialAttribute): characteristic category for the accession number.
        accession_id (str): UUID for the accession.
    """
    current_materials = getattr(node.materials, material_type_path.key)
    if not material_type_path.where:
        raise ValueError(
            f"'where' atribute is missing in path {material_type_path.key}."
        )

    updated_material = apply_filter(material_type_path.where, current_materials)

    new_material_attribute_value = MaterialAttributeValue.model_validate(
        {
            "@id": f"#material_attribute_value/accession_{accession_id}",
            "category": category,
        }
    )
    updated_material.characteristics.append(new_material_attribute_value)


def update_investigation(
    investigation: Investigation, repo_response: RepositoryResponse
) -> Investigation:
    """
    Adds the accession to the ISA JSON.

    Args:
        investigation (Investigation): The ISA JSON to be updated.
        repo_response (RepositoryResponse): The response from the repository.

    Returns:
        Investigation: The updated ISA JSON.
    """
    # TODO: Modify to include datafile related accessions as well. E.G: ENA's run accessions.
    target_repository = repo_response.target_repository
    for accession in repo_response.accessions:

        has_assay_in_path = len([p for p in accession.path if p.key == "assays"]) > 0
        has_materials_in_path = (
            len([p for p in accession.path if p.key == "materials"]) > 0
        )
        has_data_files_in_path = (
            len([p for p in accession.path if p.key == "dataFiles"]) > 0
        )
        target_level = "assay" if has_assay_in_path else "study"

        study_filter = get_filter_for_accession_key(accession, "studies")
        if not study_filter:
            raise ValueError(f"Study filter is not present in {accession.path}.")

        ################################################################################################################
        # Filter the study or assay based on the filter
        ################################################################################################################
        updated_node = apply_filter(study_filter, investigation.studies)
        if target_level == "assay":
            assay_filter = get_filter_for_accession_key(accession, "assays")
            if not assay_filter:
                raise ValueError(f"Assay filter is not present in {accession.path}.")

            updated_node = apply_filter(assay_filter, updated_node.assays)
        ################################################################################################################

        if has_materials_in_path:
            material_type_path = next(
                p
                for p in accession.path
                if p.key in ["sources", "samples", "otherMaterials"]
            )

            if not updated_node:
                raise ValueError(f"Node not found for {accession.value}.")
            if not accession_characteristic_category_present(updated_node):
                (accession_id, category) = create_accession_characteristic_category(
                    updated_node
                )
            else:
                (accession_id, category) = fetch_existing_characteristic_category(
                    updated_node
                )

            if not accession_characteristic_present(updated_node, material_type_path):
                create_accession_characteristic(
                    updated_node, material_type_path, category, accession_id
                )

            add_accession_to_material_node(
                updated_node, accession.value, material_type_path
            )
        elif has_data_files_in_path:
            data_file_filter = get_filter_for_accession_key(accession, "dataFiles")
            if not data_file_filter:
                raise ValueError(
                    f"Data file filter is not present in {accession.path}."
                )

            updated_node = apply_filter(data_file_filter, updated_node.dataFiles)
            if not accession_data_file_comment_present(updated_node):
                create_accession_data_file_comment(updated_node)

            add_accession_to_data_file_node(updated_node, accession.value)
        else:
            # A path that ends at a study or assay (no materials or data files)
            # carries the repository's study record. ENA creates one ENA study
            # per assay, so its study accession points at that assay.
            if not updated_node:
                raise ValueError(f"Node not found for {accession.value}.")

            updated_node.comments.append(
                Comment(
                    name=f"{target_repository}_study_accession",
                    value=accession.value,
                )
            )
    return investigation


def _assays_for_target_repos(
    investigation: Investigation, target_repositories: list[str] | None
) -> list[Assay]:
    """
    Returns the assays of the investigation, limited to the assays for the given
    target repositories. When no target repositories are given, all assays are returned.
    """
    return [
        assay
        for study in investigation.studies
        for assay in study.assays
        if target_repositories is None
        or detect_target_repo_comment(assay.comments).value in target_repositories
    ]


def map_data_files_to_repositories(
    files: list[str],
    investigation: Investigation,
    target_repositories: list[str] | None = None,
) -> dict[str, list[str]]:
    """
    Maps the passed data files to the target repository of the assay that references them.
    Only the assays for the given target repositories are mapped, so the data files of
    assays that are not submitted are not required.
    """
    df_map: dict[str, list[str]] = {}
    assays = _assays_for_target_repos(investigation, target_repositories)

    files_dicts = [{"full_name": f, "short_name": os.path.basename(f)} for f in files]
    remaining_files = files_dicts.copy()
    for assay in assays:
        target_repo_comment: Comment = detect_target_repo_comment(assay.comments)
        # This is an effect of everything being optional in the Comment model.
        # Should we decide to make the value mandatory, this guard clause
        # would not be necessary anymore.
        if target_repo_comment.value is None:
            raise ValueError(
                f"At least one assay in the ISA-JSON has no "
                f"'{TARGET_REPO_KEY}' comment. Mapping not possible. "
                f"Make sure all assays in the ISA-JSON have this comment!"
            )
        assay_data_files = [df.name for df in assay.dataFiles]

        # Check if the files in the ISA-JSON are present in the command
        # If not, raise an error
        for adf in assay_data_files:
            if adf not in [fd["short_name"] for fd in files_dicts]:
                raise ValueError(
                    f"""Assay for repository '{target_repo_comment.value}' has encountered a mismatch while mapping the data files to the ISA-JSON.
                Data File '{adf}' is missing in the data files passed in the command:
                {files}
                Please correct the mismatch!"""
                )
            else:
                remaining_files = [
                    fd for fd in remaining_files if fd["short_name"] != adf
                ]

        df_map[target_repo_comment.value] = [
            fd["full_name"]
            for fd in files_dicts
            if fd["short_name"] in assay_data_files
        ]

    [
        print_and_log(
            msg=(
                f"File '{rf['short_name']}' could not be mapped to any data "
                f"file in the ISA-JSON. For this reason, it will be skipped "
                f"during submission!"
            ),
            level="warning",
        )
        for rf in remaining_files
    ]

    return df_map


@dataclass
class DataFileCheckReport:
    """
    Result of comparing the data files passed to the CLI with the data files
    referenced in the ISA-JSON. Files are matched on their file name.

    Attributes:
        matched: Passed files that match a data file in the ISA-JSON.
        unreferenced: Passed files that are not referenced in the ISA-JSON.
        missing: Names of ISA-JSON data files without a matching passed file.
        duplicates: For every ISA-JSON data file name matched by more than one
            distinct passed path, the paths sharing that name.
    """

    matched: list[str] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    duplicates: dict[str, list[str]] = field(default_factory=dict)

    def log(self, header: str = "Data file check:") -> None:
        """
        Logs the report: the counts at INFO level, every unreferenced file as a
        WARNING and every missing or duplicate data file as an ERROR.
        """
        print_and_log(
            f"{header}\n"
            f"- Data files matched to a data file in the ISA JSON: {len(self.matched)}\n"
            f"- Data files not referenced in the ISA JSON: {len(self.unreferenced)}\n"
            f"- ISA JSON data files without a matching data file: {len(self.missing)}"
        )
        for file in self.unreferenced:
            print_and_log(f"Not referenced in the ISA JSON: {file}", level="warning")
        for name in self.missing:
            print_and_log(f"Missing data file: {name}", level="error")
        for name, paths in self.duplicates.items():
            print_and_log(
                f"Duplicate data file name '{name}': {', '.join(paths)}", level="error"
            )

    def to_text(self, header: str = "Data file check:") -> str:
        """Returns the report as plain text, listing the unreferenced, missing and duplicate files."""
        lines = [
            header,
            f"- Data files matched to a data file in the ISA JSON: {len(self.matched)}",
            f"- Data files not referenced in the ISA JSON: {len(self.unreferenced)}",
            f"- ISA JSON data files without a matching data file: {len(self.missing)}",
        ]
        lines += [
            f"Not referenced in the ISA JSON: {file}" for file in self.unreferenced
        ]
        lines += [f"Missing data file: {name}" for name in self.missing]
        lines += [
            f"Duplicate data file name '{name}': {', '.join(paths)}"
            for name, paths in self.duplicates.items()
        ]
        return "\n".join(lines)


def check_data_files(
    files: list[str],
    investigation: Investigation,
    target_repositories: list[str] | None = None,
) -> DataFileCheckReport:
    """
    Checks whether the data files in the ISA-JSON are matched by the passed files.

    Args:
        files (list[str]): Paths of the data files passed through the CLI.
        investigation (Investigation): The ISA-JSON investigation.
        target_repositories (list[str] | None): Only check the data files of the
            assays for these target repositories. When None, all assays are checked.

    Returns:
        DataFileCheckReport: Matched, unreferenced, missing and duplicate data files.
    """
    isa_names = {
        df.name
        for assay in _assays_for_target_repos(investigation, target_repositories)
        for df in assay.dataFiles
        if df.name
    }
    report = DataFileCheckReport()
    for file in files:
        if os.path.basename(file) in isa_names:
            report.matched.append(file)
        else:
            report.unreferenced.append(file)

    passed_names = {os.path.basename(f) for f in files}
    report.missing = sorted(isa_names - passed_names)

    paths_by_name: dict[str, list[str]] = {}
    for file in report.matched:
        paths_by_name.setdefault(os.path.basename(file), []).append(file)
    report.duplicates = {
        name: paths for name, paths in paths_by_name.items() if len(set(paths)) > 1
    }
    return report
