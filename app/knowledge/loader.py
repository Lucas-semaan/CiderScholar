"""Bounded JSON manifest and safe YAML frontmatter loading, entirely offline."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import Field, field_validator

from app.knowledge.contracts import (
    ImmutableModel,
    ItemId,
    PositiveInt,
    Version,
    portable_relative_path,
    unique,
)
from app.knowledge.models import KNOWLEDGE_ITEM_ADAPTER, KnowledgeItem, KnowledgePackage
from app.knowledge.validation import (
    MAX_PACKAGE_BYTES,
    KnowledgeIssue,
    KnowledgeValidationReport,
    validate_package,
)

MAX_FILE_BYTES = 32 * 1024
MAX_YAML_DEPTH = 10


class KnowledgeLoadError(ValueError):
    """Safe diagnostics never include input values, source text or absolute paths."""

    def __init__(self, *issues: KnowledgeIssue) -> None:
        super().__init__("knowledge package rejected")
        self.issues = issues


class PackageManifest(ImmutableModel):
    schema_version: Literal[1] = 1
    package_id: ItemId
    version: Version
    minimum_app_version: Version
    minimum_schema_version: PositiveInt
    files: Annotated[tuple[str, ...], Field(min_length=1, max_length=100)]

    @field_validator("files")
    @classmethod
    def safe_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        unique(tuple(value.casefold() for value in values), "file paths")
        for value in values:
            portable_relative_path(value)
            if Path(value).suffix != ".md":
                raise ValueError("knowledge files must use .md")
        return tuple(sorted(values))


def safe_regular_file(root: Path, relative: str) -> Path:
    portable_relative_path(relative)
    root = root.resolve(strict=True)
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise KnowledgeLoadError(KnowledgeIssue(code="linked_path", file=relative))
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not stat.S_ISREG(resolved.stat().st_mode):
        raise KnowledgeLoadError(KnowledgeIssue(code="unsafe_file", file=relative))
    return resolved


def _read_file(root: Path, relative: str, limit: int = MAX_FILE_BYTES) -> bytes:
    try:
        path = safe_regular_file(root, relative)
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
    except KnowledgeLoadError:
        raise
    except (OSError, ValueError) as error:
        raise KnowledgeLoadError(KnowledgeIssue(code="file_unavailable", file=relative)) from error
    if len(content) > limit:
        raise KnowledgeLoadError(KnowledgeIssue(code="file_too_large", file=relative))
    return content


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise KnowledgeLoadError(KnowledgeIssue(code="duplicate_json_key", file="package.json"))
        result[key] = value
    return result


class _StrictYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise KnowledgeLoadError(KnowledgeIssue(code="invalid_yaml_mapping"))
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise KnowledgeLoadError(KnowledgeIssue(code="invalid_yaml_key"))
            if key in result:
                raise KnowledgeLoadError(KnowledgeIssue(code="duplicate_yaml_key"))
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _frontmatter(text: str) -> dict[str, object]:
    lines = text.replace("\r\n", "\n").splitlines(keepends=True)
    if not lines or lines[0].strip("\n") != "---":
        raise KnowledgeLoadError(KnowledgeIssue(code="missing_frontmatter"))
    end = next((index for index in range(1, len(lines)) if lines[index].strip("\n") == "---"), None)
    if end is None:
        raise KnowledgeLoadError(KnowledgeIssue(code="missing_frontmatter_end"))
    frontmatter = "".join(lines[1:end])
    depth = 0
    for event in yaml.parse(frontmatter, Loader=_StrictYamlLoader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise KnowledgeLoadError(KnowledgeIssue(code="yaml_alias_or_anchor"))
        if getattr(event, "tag", None) is not None:
            raise KnowledgeLoadError(KnowledgeIssue(code="yaml_explicit_tag"))
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
            if depth > MAX_YAML_DEPTH:
                raise KnowledgeLoadError(KnowledgeIssue(code="yaml_depth_exceeded"))
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1
    payload = yaml.load(frontmatter, Loader=_StrictYamlLoader)
    if not isinstance(payload, dict):
        raise KnowledgeLoadError(KnowledgeIssue(code="invalid_frontmatter"))
    if "body" in payload:
        raise KnowledgeLoadError(KnowledgeIssue(code="duplicate_body"))
    return {**payload, "body": "".join(lines[end + 1 :])}


def load_package(knowledge_dir: Path, *, source_root: Path | None = None) -> KnowledgePackage:
    """Read only listed files and verify all provenance hashes against the local checkout."""

    source_root = source_root if source_root is not None else knowledge_dir.parent
    raw_manifest = _read_file(knowledge_dir, "package.json")
    try:
        manifest = PackageManifest.model_validate(
            json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_unique_json_object)
        )
    except KnowledgeLoadError:
        raise
    except (ValueError, RecursionError) as error:
        raise KnowledgeLoadError(
            KnowledgeIssue(code="invalid_manifest", file="package.json")
        ) from error
    items: list[KnowledgeItem] = []
    known_ids: set[str] = set()
    total_bytes = len(raw_manifest)
    provenance_cache: dict[str, str] = {}
    for relative in manifest.files:
        raw = _read_file(knowledge_dir, relative)
        total_bytes += len(raw)
        if total_bytes > MAX_PACKAGE_BYTES:
            raise KnowledgeLoadError(KnowledgeIssue(code="package_too_large"))
        try:
            item = KNOWLEDGE_ITEM_ADAPTER.validate_python(_frontmatter(raw.decode("utf-8")))
        except KnowledgeLoadError as error:
            raise KnowledgeLoadError(
                *(issue.model_copy(update={"file": relative}) for issue in error.issues)
            ) from error
        except (ValueError, yaml.YAMLError, RecursionError) as error:
            raise KnowledgeLoadError(
                KnowledgeIssue(code="invalid_knowledge_item", file=relative)
            ) from error
        if item.id in known_ids:
            raise KnowledgeLoadError(KnowledgeIssue(code="duplicate_item_id", item_id=item.id))
        known_ids.add(item.id)
        for provenance in item.provenance:
            if provenance.document not in provenance_cache:
                source = _read_file(source_root, provenance.document, limit=MAX_PACKAGE_BYTES)
                provenance_cache[provenance.document] = hashlib.sha256(source).hexdigest()
            if provenance_cache[provenance.document] != provenance.revision_sha256:
                raise KnowledgeLoadError(
                    KnowledgeIssue(code="provenance_changed", item_id=item.id, field="provenance")
                )
        items.append(item)
    package = KnowledgePackage(**manifest.model_dump(exclude={"files"}), items=items)
    report = validate_package(package)
    if not report.structurally_valid:
        raise KnowledgeLoadError(*report.issues)
    return package


def lint_package(
    knowledge_dir: Path, *, source_root: Path | None = None
) -> KnowledgeValidationReport:
    try:
        return validate_package(load_package(knowledge_dir, source_root=source_root))
    except KnowledgeLoadError as error:
        return KnowledgeValidationReport(structurally_valid=False, issues=error.issues)
