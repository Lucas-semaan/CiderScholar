"""First-launch state derived from verified local resources, never from unchecked flags."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.corpus_packages.actions import (
    download_and_validate_available_update,
    mark_validated_update_ready,
)
from app.corpus_packages.activation import activate_ready_update_at_startup
from app.corpus_packages.distribution import DistributionPathError
from app.corpus_packages.updates import LatestState, read_installed_state, read_latest_manifest
from app.desktop.model_integrity import ModelIntegrityError, verify_model_manifest
from app.desktop.system_checks import check_disk_capacity, required_installation_bytes
from app.desktop.user_config import (
    memory_profile_overrides,
    packaged_config_path,
    write_user_overrides,
)
from app.ingestion.embeddings import local_model_path
from app.llm.argo_key import ArgoKeyStore
from app.memory_profiles import MemoryProfileName, recommend_memory_profile

BUNDLED_CORPUS_MARKER = ".ciderscholar-bundled-corpus"


class OnboardingError(RuntimeError):
    """One first-launch prerequisite is incomplete or invalid."""


class OnboardingStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    model_ready: bool
    sharepoint_ready: bool
    corpus_ready: bool
    argo_ready: bool
    memory_ready: bool
    completed: bool
    synchronized_root: str | None
    installed_corpus_version: str | None
    memory: dict[str, object]


def _bundled_corpus_version(settings: Settings) -> str | None:
    """Recognize the verified base corpus copied atomically by the installer."""

    marker = settings.paths.common_dir / BUNDLED_CORPUS_MARKER
    database = settings.paths.common_database_path
    qdrant = settings.paths.common_qdrant_dir
    if not marker.is_file() or not database.is_file():
        return None
    if not any(path.is_file() and path.name != ".lock" for path in qdrant.rglob("*")):
        return None
    try:
        version = marker.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return version or "default-rag"


def onboarding_status(settings: Settings) -> OnboardingStatus:
    try:
        verify_model_manifest(local_model_path(settings), settings.embeddings.model_name)
        model_ready = True
    except ModelIntegrityError:
        model_ready = False
    latest = read_latest_manifest(settings)
    sharepoint_ready = latest.state is LatestState.AVAILABLE
    installed = read_installed_state(settings)
    bundled_version = _bundled_corpus_version(settings)
    corpus_version = installed.corpus_version if installed is not None else bundled_version
    corpus_ready = corpus_version is not None
    argo_ready = ArgoKeyStore(settings).configured()
    memory = recommend_memory_profile(settings)
    memory_ready = settings.memory.profile in {profile.value for profile in MemoryProfileName}
    requirements = (model_ready, corpus_ready, argo_ready, memory_ready)
    return OnboardingStatus(
        model_ready=model_ready,
        sharepoint_ready=sharepoint_ready,
        corpus_ready=corpus_ready,
        argo_ready=argo_ready,
        memory_ready=memory_ready,
        completed=all(requirements),
        synchronized_root=(
            str(settings.distribution.synchronized_root)
            if settings.distribution.synchronized_root is not None
            else None
        ),
        installed_corpus_version=corpus_version,
        memory=asdict(memory),
    )


def configure_synchronized_root(
    settings: Settings,
    candidate: Path,
    *,
    confirm_unexpected_name: bool,
) -> Settings:
    root = candidate.resolve()
    if not root.is_dir():
        raise OnboardingError("Le dossier synchronisé sélectionné n'existe pas.")
    if root == settings.paths.data_dir or root.is_relative_to(settings.paths.data_dir):
        raise OnboardingError("Le dossier SharePoint doit rester hors des données locales.")
    expected = settings.distribution.expected_folder_name.casefold()
    if root.name.casefold() != expected and not confirm_unexpected_name:
        raise DistributionPathError("Le nom inattendu du dossier exige une confirmation explicite.")
    distribution = settings.distribution.model_copy(
        update={"enabled": True, "synchronized_root": root}
    )
    updated = settings.model_copy(deep=True, update={"distribution": distribution})
    latest = read_latest_manifest(updated)
    if latest.state is not LatestState.AVAILABLE:
        raise OnboardingError(latest.message)
    write_user_overrides(
        packaged_config_path(),
        {
            "distribution": {
                "enabled": True,
                "synchronized_root": str(root),
            }
        },
    )
    return updated


def install_first_common_corpus(settings: Settings) -> Settings:
    latest = read_latest_manifest(settings)
    if latest.state is not LatestState.AVAILABLE or latest.manifest is None:
        raise OnboardingError(latest.message)
    model = verify_model_manifest(local_model_path(settings), settings.embeddings.model_name)
    required = required_installation_bytes(latest.manifest.archive.size_bytes, model.total_bytes)
    check_disk_capacity(settings.paths.data_dir, required)
    download_and_validate_available_update(settings)
    mark_validated_update_ready(settings)
    activated = activate_ready_update_at_startup(settings)
    if activated is None:
        raise OnboardingError("Le corpus vérifié n'a pas pu être activé.")
    return settings


def select_memory_profile(settings: Settings, profile: MemoryProfileName) -> Settings:
    updated, overrides = memory_profile_overrides(settings, profile)
    write_user_overrides(packaged_config_path(), overrides)
    return updated
