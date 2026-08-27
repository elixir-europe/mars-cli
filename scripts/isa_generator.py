from __future__ import annotations

from pathlib import Path
from datetime import datetime, UTC
import uuid
import json
import gzip
import hashlib
from typing import Any, List, Tuple


def _timestamp_suffix() -> str:
    """Unique suffix for this run (used for data file names)."""
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]


def _write_dummy_data_file(path: Path) -> None:
    """Write a tiny dummy file, using gzip for compressed FASTQ extensions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "@read1\nACGTACGTACGTACGT\n+\nFFFFFFFFFFFFFFFF\n"
    if path.name.lower().endswith((".fastq.gz", ".fq.gz")):
        with gzip.open(path, "wt") as fh:
            fh.write(content)
    else:
        path.write_text(content)


def _add_suffix_before_extension(file_name: str, suffix: str) -> str:
    """Insert a unique suffix while preserving the original file extension."""
    lower_name = file_name.lower()
    for compound_extension in (".fastq.gz", ".fq.gz"):
        if lower_name.endswith(compound_extension):
            extension = file_name[-len(compound_extension) :]
            return f"{file_name[:-len(compound_extension)]}_{suffix}{extension}"

    extension = Path(file_name).suffix
    if extension:
        return f"{file_name[:-len(extension)]}_{suffix}{extension}"
    return f"{file_name}_{suffix}"


def _md5_of_file(path: Path) -> str:
    """
    Compute MD5 checksum of the given file (binary content).
    """
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_all_assays(isa_obj: dict[str, Any]) -> List[dict[str, Any]]:
    """
    Return all assays found under investigation.studies[*].assays[*].
    """
    inv = isa_obj.get("investigation")
    if inv is None:
        inv = isa_obj

    if not isinstance(inv, dict):
        return []

    studies = inv.get("studies") or []
    if not isinstance(studies, list):
        return []

    assays: List[dict[str, Any]] = []
    for study in studies:
        if not isinstance(study, dict):
            continue

        study_assays = study.get("assays") or []
        if not isinstance(study_assays, list):
            continue

        assays.extend(assay for assay in study_assays if isinstance(assay, dict))

    return assays


def _ensure_comment(comments: List[dict[str, Any]], name: str, value: str) -> None:
    """
    Ensure there is a comment with the given name, updating it if it exists,
    or appending a new one if not.
    """
    for c in comments:
        if isinstance(c, dict) and c.get("name") == name:
            c["value"] = value
            return
    comments.append({"name": name, "value": value})


def _update_datafiles_with_generated_files(
    assays: List[dict[str, Any]],
    data_dir: Path,
    n_files: int | None,
) -> List[Path]:
    """
    Update assay dataFiles entries with newly generated files.

    Behaviour per touched data file:

      - Generate a unique file based on the existing 'name' while preserving
        its extension, for example:
          ENA_TEST2.R2.fastq.gz -> ENA_TEST2.R2_<suffix>.fastq.gz
          reads.R1.fq.gz         -> reads.R1_<suffix>.fq.gz

      - Write a dummy FASTQ into that file and compute its MD5.

      - Update the dataFiles entry:
          * "name" = new file name
          * in "comments":
              - "file name"       -> new file name
              - "file type"       -> the preserved extension
              - "file checksum"   -> MD5 of the generated file
              - "checksum_method" -> "MD5"
            (existing "accession", "submission date", etc. are kept as-is)
    """
    generated_paths: List[Path] = []
    suffix = _timestamp_suffix()
    updated_count = 0

    for assay in assays:
        data_files_json = assay.get("dataFiles") or []
        if not isinstance(data_files_json, list):
            continue

        for df_json in data_files_json:
            if n_files is not None and updated_count >= n_files:
                return generated_paths
            if not isinstance(df_json, dict):
                continue

            original_name = df_json.get("name")
            if not isinstance(original_name, str) or not original_name:
                continue

            # ISA data file names can include a relative directory (HoloFood uses
            # FILES/RAW_FILES/...). Upload mapping is filename-based, so generated
            # PoC files and their ISA names must use only the basename.
            original_basename = original_name.replace("\\", "/").rsplit("/", 1)[-1]

            new_name = _add_suffix_before_extension(original_basename, suffix)

            file_path = data_dir / new_name
            _write_dummy_data_file(file_path)
            md5 = _md5_of_file(file_path)

            df_json["name"] = new_name

            comments = df_json.get("comments")
            if not isinstance(comments, list):
                comments = []
                df_json["comments"] = comments

            _ensure_comment(comments, "file name", new_name)
            file_type = (
                "fastq"
                if new_name.lower().endswith((".fastq.gz", ".fq.gz"))
                else Path(new_name).suffix.lstrip(".") or "data"
            )
            _ensure_comment(comments, "file type", file_type)
            _ensure_comment(comments, "file checksum", md5)
            _ensure_comment(comments, "checksum_method", "MD5")

            generated_paths.append(file_path)
            updated_count += 1

    return generated_paths


def generate_isa_json_with_data(
    work_dir: Path,
    template_path: Path,
    n_files: int | None = None,
) -> Tuple[Path, List[Path]]:
    """
    PoC behaviour:

      1. Load ISA-JSON template from template_path.
      2. Find all investigation.studies[*].assays[*].dataFiles.
      3. For each data file (or the first n_files when limited), generate UNIQUE
         files with preserved extensions and update:
           - dataFiles[i]["name"]
           - dataFiles[i]["comments"] entries for file name, type, checksum, method.
      4. Write the resulting ISA-JSON to work_dir / 'isa.json'.

    We DO NOT change other identifiers or comments (including 'target_repository').
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    isa_obj = json.loads(template_path.read_text())

    assays = _get_all_assays(isa_obj)
    generated_paths: List[Path] = []
    if assays:
        data_dir = work_dir / "data"
        generated_paths = _update_datafiles_with_generated_files(
            assays=assays,
            data_dir=data_dir,
            n_files=n_files,
        )

    isa_path = work_dir / "isa.json"
    isa_path.write_text(json.dumps(isa_obj, indent=2))

    return isa_path, generated_paths
