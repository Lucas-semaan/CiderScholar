"""Read locally synchronized update metadata without network calls."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app import __version__
from app.config import Settings
from app.corpus_packages.hashing import sha256_file
from app.corpus_packages.models import CorpusManifest, LatestCorpusPointer


class LatestState(StrEnum):
    DISABLED = "disabled"
    SYNC_UNAVAILABLE = "sync_unavailable"
    LATEST_UNAVAILABLE = "latest_unavailable"
    INVALID = "invalid"
    AVAILABLE = "available"


class LatestReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: LatestState
    message: str
    pointer: LatestCorpusPointer | None = None
    manifest: CorpusManifest | None = None
    manifest_path: str | None = None


class InstalledCorpusState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    installed_at: datetime
    manifest_sha256: str


class UpdateComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latest_state: LatestState
    installed_version: str | None
    available_version: str | None
    update_available: bool
    download_required: bool
    message: str


class AppCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compatible: bool
    current_app_version: str
    minimum_app_version: str
    message: str


def read_latest_manifest(settings: Settings) -> LatestReadResult:
    """Distinguish disabled configuration, missing sync, missing latest and corruption."""

    root = settings.distribution.synchronized_root
    if not settings.distribution.enabled or root is None:
        return LatestReadResult(
            state=LatestState.DISABLED,
            message="La distribution du corpus n'est pas configurée.",
        )
    synchronized = root.resolve()
    if not synchronized.is_dir():
        return LatestReadResult(
            state=LatestState.SYNC_UNAVAILABLE,
            message="Le dossier OneDrive/SharePoint n'est pas synchronisé sur ce poste.",
        )
    latest_path = synchronized / "corpus" / "latest.json"
    if not latest_path.is_file():
        return LatestReadResult(
            state=LatestState.LATEST_UNAVAILABLE,
            message="Aucun pointeur latest synchronisé n'est disponible.",
        )
    try:
        pointer = LatestCorpusPointer.model_validate_json(latest_path.read_bytes())
        manifest_path = synchronized / "corpus" / Path(pointer.manifest_relative_path)
        if not manifest_path.is_file() or sha256_file(manifest_path) != pointer.manifest_sha256:
            raise ValueError("latest manifest hash mismatch")
        manifest = CorpusManifest.model_validate_json(manifest_path.read_bytes())
        if manifest.corpus_version != pointer.corpus_version:
            raise ValueError("latest manifest version mismatch")
    except (OSError, ValueError) as exc:
        return LatestReadResult(
            state=LatestState.INVALID,
            message=f"Les métadonnées de mise à jour sont invalides ({type(exc).__name__}).",
        )
    return LatestReadResult(
        state=LatestState.AVAILABLE,
        message="Une version synchronisée du corpus est disponible.",
        pointer=pointer,
        manifest=manifest,
        manifest_path=str(manifest_path),
    )


def installed_state_path(settings: Settings) -> Path:
    return settings.paths.common_dir / "installed.json"


def read_installed_state(settings: Settings) -> InstalledCorpusState | None:
    path = installed_state_path(settings)
    if not path.is_file():
        return None
    return InstalledCorpusState.model_validate_json(path.read_bytes())


def write_installed_state(settings: Settings, state: InstalledCorpusState) -> Path:
    path = installed_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    try:
        temporary.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def compare_corpus_versions(settings: Settings) -> UpdateComparison:
    latest = read_latest_manifest(settings)
    installed = read_installed_state(settings)
    installed_version = installed.corpus_version if installed else None
    if latest.state is not LatestState.AVAILABLE or latest.manifest is None:
        return UpdateComparison(
            latest_state=latest.state,
            installed_version=installed_version,
            available_version=None,
            update_available=False,
            download_required=False,
            message=latest.message,
        )
    available = latest.manifest.corpus_version
    differs = installed_version != available
    return UpdateComparison(
        latest_state=latest.state,
        installed_version=installed_version,
        available_version=available,
        update_available=differs,
        download_required=differs,
        message=(
            "Une nouvelle version du corpus est disponible."
            if differs
            else "Le corpus installé est déjà à jour."
        ),
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+][0-9A-Za-z.-]+)?", value)
    if match is None:
        raise ValueError(f"invalid application version: {value}")
    return tuple(int(part) for part in match.groups())


def check_app_compatibility(
    manifest: CorpusManifest,
    *,
    current_app_version: str = __version__,
) -> AppCompatibility:
    compatible = _version_tuple(current_app_version) >= _version_tuple(manifest.minimum_app_version)
    return AppCompatibility(
        compatible=compatible,
        current_app_version=current_app_version,
        minimum_app_version=manifest.minimum_app_version,
        message=(
            "La version de l'application est compatible avec ce corpus."
            if compatible
            else (
                f"Mise à jour de l'application requise : version minimale "
                f"{manifest.minimum_app_version}, version installée {current_app_version}."
            )
        ),
    )
