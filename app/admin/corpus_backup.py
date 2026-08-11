"""Verified pre-maintenance corpus backup and local rollback operations."""

from __future__ import annotations

import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.corpora import LocalProfile
from app.corpus_packages.builder import build_corpus_package
from app.corpus_packages.installer import CorpusInstallError, safe_archive_destination
from app.corpus_packages.models import CorpusManifest
from app.corpus_packages.publisher import archive_published_package
from app.services.corpus_updates import activate_prepared_common_corpus


class MaintenanceBackup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    version_directory: str
    protected_directory: str
    archive_sha256: str


def create_maintenance_backup(
    settings: Settings,
    maintenance_id: UUID,
) -> MaintenanceBackup:
    """Build an openable package before mutation and copy it to the protected drive."""

    # A corpus version includes a long content hash.  Keep the internal build
    # root deliberately short so Windows path APIs can still inspect the
    # resulting manifest when the configured data directory is itself nested.
    # The returned version directory remains the sole rollback authority.
    output = settings.paths.data_dir / ".b" / maintenance_id.hex[:16]
    built = build_corpus_package(settings, output_root=output)
    version_directory = Path(built.version_directory)
    protected = archive_published_package(
        settings,
        version_directory,
        profile=LocalProfile.ADMIN,
    )
    if protected.archive_sha256 != built.manifest.archive.sha256:
        raise RuntimeError("Le hash de la sauvegarde protégée ne correspond pas au paquet local.")
    return MaintenanceBackup(
        corpus_version=built.manifest.corpus_version,
        version_directory=str(version_directory),
        protected_directory=protected.version_directory,
        archive_sha256=protected.archive_sha256,
    )


def rollback_maintenance_backup(
    settings: Settings,
    maintenance_id: UUID,
    backup: MaintenanceBackup,
) -> Path:
    """Verify and atomically reactivate the pre-maintenance common package."""

    version = Path(backup.version_directory).resolve()
    manifest = CorpusManifest.model_validate_json((version / "manifest.json").read_bytes())
    archive_path = version / manifest.archive.filename
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != backup.archive_sha256:
        raise CorpusInstallError("Le hash de la sauvegarde de rollback est invalide.")
    # Keep the extraction path short for the same Windows path-budget reason as
    # the immutable package build above.  UUIDs retain collision resistance;
    # neither directory name is part of the verified backup contract.
    root = settings.paths.data_dir / ".r" / maintenance_id.hex[:16]
    extracted = root / f"x-{uuid.uuid4().hex[:16]}"
    extracted.mkdir(parents=True)
    try:
        expected = {artifact.relative_path: artifact for artifact in manifest.artifacts}
        with zipfile.ZipFile(archive_path) as archive:
            if set(archive.namelist()) != set(expected):
                raise CorpusInstallError("La sauvegarde contient des artefacts inattendus.")
            for name, artifact in expected.items():
                payload = archive.read(name)
                if (
                    len(payload) != artifact.size_bytes
                    or hashlib.sha256(payload).hexdigest() != artifact.sha256
                ):
                    raise CorpusInstallError(f"Artefact de rollback invalide : {name}")
                destination = safe_archive_destination(extracted, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
        swap = activate_prepared_common_corpus(
            settings,
            extracted,
            profile=LocalProfile.ADMIN,
        )
        return swap.activated_path
    finally:
        if extracted.exists():
            shutil.rmtree(extracted)
