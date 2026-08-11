"""Atomic filesystem publication of a hashed application installer to SharePoint sync."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.desktop.app_updates import ApplicationReleaseManifest
from app.file_integrity import sha256_file


class ApplicationPublishError(RuntimeError):
    """The local installer release is incomplete, altered or conflicts with publication."""


def _verified_release(source: Path) -> tuple[ApplicationReleaseManifest, Path, Path]:
    try:
        manifest_path = source / "latest.json"
        manifest = ApplicationReleaseManifest.model_validate_json(manifest_path.read_bytes())
        installer = source / manifest.filename
        checksum = installer.with_suffix(f"{installer.suffix}.sha256")
        expected_line = f"{manifest.sha256}  {manifest.filename}"
        if (
            not installer.is_file()
            or installer.stat().st_size != manifest.size_bytes
            or sha256_file(installer) != manifest.sha256
            or not checksum.is_file()
            or checksum.read_text(encoding="ascii").strip() != expected_line
        ):
            raise ApplicationPublishError("installer release hash or companion file mismatch")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ApplicationPublishError):
            raise
        raise ApplicationPublishError("installer release metadata is invalid") from exc
    return manifest, installer, checksum


def publish_application_release(source: Path, synchronized_root: Path) -> Path:
    """Expose immutable files first and atomically replace latest.json last."""

    release = source.resolve()
    root = synchronized_root.resolve()
    if not root.is_dir():
        raise ApplicationPublishError("synchronized SharePoint root is unavailable")
    manifest, installer, checksum = _verified_release(release)
    installers = root / "installers"
    installers.mkdir(parents=True, exist_ok=True)
    staging = installers / f".publish-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        staged_installer = staging / installer.name
        staged_checksum = staging / checksum.name
        shutil.copy2(installer, staged_installer)
        shutil.copy2(checksum, staged_checksum)
        if sha256_file(staged_installer) != manifest.sha256:
            raise ApplicationPublishError("staged installer hash mismatch")
        immutable_artifacts = (
            (staged_installer, manifest.sha256),
            (staged_checksum, sha256_file(staged_checksum)),
        )
        for staged, expected_sha256 in immutable_artifacts:
            destination = installers / staged.name
            if destination.exists():
                if (
                    destination.stat().st_size != staged.stat().st_size
                    or sha256_file(destination) != expected_sha256
                ):
                    raise ApplicationPublishError("immutable published filename already differs")
                staged.unlink()
            else:
                staged.replace(destination)
        latest_temporary = installers / f".latest.{uuid.uuid4().hex[:8]}.tmp"
        shutil.copy2(release / "latest.json", latest_temporary)
        latest_temporary.replace(installers / "latest.json")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return installers / "latest.json"
