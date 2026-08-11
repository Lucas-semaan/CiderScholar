"""Read-only application release discovery from the synchronized installers directory."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.config import Settings
from app.file_integrity import sha256_file
from app.jobs.repository import JobRepository


class ApplicationUpdateState(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    CURRENT = "current"
    AVAILABLE = "available"
    DEFERRED_ACTIVE_JOBS = "deferred_active_jobs"


class ApplicationReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    filename: str = Field(pattern=r"^[A-Za-z0-9._-]+\.exe$")
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_windows_build: int = Field(ge=22000)
    published_at: datetime


class ApplicationUpdateStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: ApplicationUpdateState
    installed_version: str
    available_version: str | None
    installer_path: str | None
    active_jobs: int = Field(ge=0)
    message: str


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", value)
    if match is None:
        raise ValueError("invalid application version")
    return tuple(int(part) for part in match.groups())


def _base_status(state: ApplicationUpdateState, message: str) -> ApplicationUpdateStatus:
    return ApplicationUpdateStatus(
        state=state,
        installed_version=__version__,
        available_version=None,
        installer_path=None,
        active_jobs=0,
        message=message,
    )


def check_application_update(settings: Settings) -> ApplicationUpdateStatus:
    """Verify the installer and defer its use while the durable queue is active."""

    root = settings.distribution.synchronized_root
    if not settings.distribution.enabled or root is None:
        return _base_status(
            ApplicationUpdateState.DISABLED,
            "La vérification applicative est inactive tant que SharePoint n'est pas configuré.",
        )
    latest = root.resolve() / "installers" / "latest.json"
    if not latest.is_file():
        return _base_status(
            ApplicationUpdateState.UNAVAILABLE,
            "Aucune version applicative synchronisée n'est publiée.",
        )
    try:
        manifest = ApplicationReleaseManifest.model_validate_json(latest.read_bytes())
        if PurePath(manifest.filename).name != manifest.filename:
            raise ValueError("unsafe installer filename")
        installer = latest.parent / manifest.filename
        if (
            not installer.is_file()
            or installer.stat().st_size != manifest.size_bytes
            or sha256_file(installer) != manifest.sha256
        ):
            raise ValueError("installer hash mismatch")
        _version_tuple(manifest.version)
    except (OSError, ValueError):
        return _base_status(
            ApplicationUpdateState.INVALID,
            "Les métadonnées ou le hash de l'installateur sont invalides.",
        )
    active = JobRepository(settings.paths.database_path).active_job_count()
    update_available = _version_tuple(manifest.version) > _version_tuple(__version__)
    if not update_available:
        return ApplicationUpdateStatus(
            state=ApplicationUpdateState.CURRENT,
            installed_version=__version__,
            available_version=manifest.version,
            installer_path=str(installer),
            active_jobs=active,
            message="L'application installée est à jour.",
        )
    if active:
        return ApplicationUpdateStatus(
            state=ApplicationUpdateState.DEFERRED_ACTIVE_JOBS,
            installed_version=__version__,
            available_version=manifest.version,
            installer_path=None,
            active_jobs=active,
            message="Mise à jour reportée : un travail durable est encore actif.",
        )
    return ApplicationUpdateStatus(
        state=ApplicationUpdateState.AVAILABLE,
        installed_version=__version__,
        available_version=manifest.version,
        installer_path=str(installer),
        active_jobs=0,
        message="Une mise à jour applicative vérifiée est disponible dans SharePoint.",
    )
