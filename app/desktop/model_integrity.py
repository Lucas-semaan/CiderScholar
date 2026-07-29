"""Content-addressed manifest for any bundled local inference model."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MODEL_MANIFEST = "ciderscholar-model-manifest.json"


class ModelIntegrityError(RuntimeError):
    """An installed model is missing, altered or unexpectedly shaped."""


class ModelFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    model_name: str = Field(min_length=1, max_length=300)
    files: dict[str, ModelFile] = Field(min_length=1)
    total_bytes: int = Field(gt=0)


def _digest(path: Path) -> ModelFile:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return ModelFile(size_bytes=size, sha256=digest.hexdigest())


def _model_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != MODEL_MANIFEST and not path.is_symlink()
    )


def build_model_manifest(model_root: Path, model_name: str) -> ModelManifest:
    """Hash every regular model file using portable relative names."""

    root = model_root.resolve()
    if not root.is_dir() or any(path.is_symlink() for path in root.rglob("*")):
        raise ModelIntegrityError("model directory is missing or contains a link")
    files = {path.relative_to(root).as_posix(): _digest(path) for path in _model_files(root)}
    if not files:
        raise ModelIntegrityError("model directory is empty")
    return ModelManifest(
        model_name=model_name,
        files=files,
        total_bytes=sum(entry.size_bytes for entry in files.values()),
    )


def write_model_manifest(model_root: Path, model_name: str) -> Path:
    """Atomically persist the model manifest next to the model it covers."""

    root = model_root.resolve()
    manifest = build_model_manifest(root, model_name)
    destination = root / MODEL_MANIFEST
    temporary = root / f".{MODEL_MANIFEST}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        temporary.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def verify_model_manifest(
    model_root: Path, expected_model_name: str | None = None
) -> ModelManifest:
    """Reject missing, extra, unsafe or hash-mismatched model files."""

    root = model_root.resolve()
    manifest_path = root / MODEL_MANIFEST
    try:
        manifest = ModelManifest.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ModelIntegrityError("model manifest is missing or invalid") from exc
    if expected_model_name is not None and manifest.model_name != expected_model_name:
        raise ModelIntegrityError("model identity does not match configuration")
    actual_names = {path.relative_to(root).as_posix() for path in _model_files(root)}
    if actual_names != set(manifest.files):
        raise ModelIntegrityError("model file list does not match its manifest")
    for name, expected in manifest.files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ModelIntegrityError("model manifest contains an unsafe path")
        actual = _digest(root.joinpath(*relative.parts))
        if actual != expected:
            raise ModelIntegrityError(f"model hash mismatch: {name}")
    return manifest
