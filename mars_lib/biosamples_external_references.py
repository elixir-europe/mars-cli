#!/usr/bin/env python3

from typing import Any

from mars_lib.isa_json import detect_target_repo_comment
from mars_lib.models.isa_json import IsaJson, Assay, Sample
from requests import Response, HTTPError

from mars_lib.target_repo import TargetRepository


def _extract_accessions(isa_json: IsaJson) -> list[dict[str, Any]]:
    studies = []

    for study in isa_json.investigation.studies:
        study_samples = study.materials.samples
        study_dict = {'title': study.title, 'id': study.id}
        accessions = [
            {
                'id': assay.id,
                'title': assay.title,
                'accession': _fetch_assay_accession(assay),
                'target_repo': _fetch_assay_target_repo(assay),
                'biosamples': _biosamples_used_in_assay(study_samples, assay)
             }
            for assay in study.assays
        ]
        study_dict.update({'assays': accessions})
        studies.append(study_dict)

    return studies

def _biosamples_used_in_assay(all_biosamples: list[Sample], assay: Assay) -> list[Sample]:
    filter(
        lambda sample: , all_biosamples)

def _fetch_assay_target_repo(assay:Assay) -> str:
    return detect_target_repo_comment(assay.comments).value

def _fetch_assay_accession(assay: Assay) -> str | ValueError:
    accession_comment = next(
        filter(
            lambda comment: "assay_accession" in comment.name.lower(),
            assay.comments
        ),
        None
    )
    if accession_comment is None:
        return ValueError(f"Accession characteristic not found in assay '[{assay.id}] - {assay.title}'!")

    return accession_comment.value

def _get_accession_comment_from_a(material: Material) -> str:
    accession_characteristic = next(
        filter(
            lambda characteristic: characteristic.category.characteristcType.annotationValue.lower() == 'accession',
            material.characteristics
        )
    )
    return accession_characteristic.value

def update_external_references(isa_json: IsaJson, urls_dict: dict) -> Response | HTTPError | ValueError:
    accessions_dict = _extract_accessions(isa_json)
    biosamples_url = urls_dict.get("BIOSAMPLES", {}).get("SERVICE", {})
    for study in accessions_dict:
        for assay in study.assays:
            url = urls_dict.get(TargetRepository(assay['target_repo']), {}).get('EXTERNAL-REF-URL', None)
            if url is None:
                return ValueError(f"No 'EXTERNAL-REF-URL' found for target repository '{assay['target_repo']}' in assay '{assay['title']}'.")

