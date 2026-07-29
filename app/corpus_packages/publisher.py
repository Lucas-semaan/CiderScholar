"""Atomic filesystem publication through a locally synchronized SharePoint folder."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.corpora import CorpusScope, LocalProfile, authorize_corpus_mutation
from app.corpus_packages.distribution import (
    create_distribution_layout,
    validate_distribution_root,
)
from app.corpus_packages.hashing import sha256_file
from app.corpus_packages.models import CorpusManifest, LatestCorpusPointer
from app.corpus_packages.signatures import (
    PackageSignatureError,
    verify_corpus_package_signatures,
)

PublishEvent = Callable[[str], None]


class CorpusPublishError(RuntimeError):
    """An immutable version cannot be verified or published safely."""


class CorpusPublishReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_directory: str
    latest_path: str
    pointer: LatestCorpusPointer


class CorpusArchiveReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_directory: str
    archive_sha256: str


def _verified_manifest(version_directory: Path) -> tuple[CorpusManifest, Path]:
    manifest_path = version_directory / "manifest.json"
    if not manifest_path.is_file():
        raise CorpusPublishError("package manifest is unavailable")
    manifest = CorpusManifest.model_validate_json(manifest_path.read_bytes())
    if version_directory.name != manifest.corpus_version:
        raise CorpusPublishError("package directory does not match its immutable version")
    archive = version_directory / manifest.archive.filename
    if (
        not archive.is_file()
        or archive.stat().st_size != manifest.archive.size_bytes
        or sha256_file(archive) != manifest.archive.sha256
    ):
        raise CorpusPublishError("package archive does not match its manifest")
    return manifest, manifest_path


def publish_corpus_package(
    settings: Settings,
    version_directory: Path,
    *,
    profile: LocalProfile,
    explicit_path_confirmation: bool = False,
    on_event: PublishEvent | None = None,
) -> CorpusPublishReport:
    """Expose a complete immutable version before atomically updating latest.json."""

    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    if settings.distribution.signature_required:
        try:
            verify_corpus_package_signatures(
                version_directory,
                allowed_signers=settings.distribution.allowed_signers_path,
            )
        except PackageSignatureError as error:
            raise CorpusPublishError(str(error)) from error
    manifest, _ = _verified_manifest(version_directory.resolve())
    distribution_root = validate_distribution_root(
        settings,
        explicit_confirmation=explicit_path_confirmation,
    )
    corpus_root = create_distribution_layout(distribution_root).corpus
    destination = corpus_root / manifest.corpus_version
    if destination.exists():
        existing, _ = _verified_manifest(destination)
        if existing != manifest:
            raise CorpusPublishError("published immutable version has different content")
    else:
        staging_root = corpus_root / f".p-{uuid.uuid4().hex[:8]}"
        staging = staging_root / manifest.corpus_version
        try:
            staging_root.mkdir()
            shutil.copytree(version_directory, staging)
            copied, _ = _verified_manifest(staging)
            if copied != manifest:
                raise CorpusPublishError("copied package differs from its source manifest")
            if settings.distribution.signature_required:
                verify_corpus_package_signatures(
                    staging,
                    allowed_signers=settings.distribution.allowed_signers_path,
                )
            staging.replace(destination)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)
    if on_event is not None:
        on_event("version_ready")

    published_manifest = destination / "manifest.json"
    pointer = LatestCorpusPointer(
        corpus_version=manifest.corpus_version,
        published_at=manifest.published_at,
        manifest_relative_path=f"{manifest.corpus_version}/manifest.json",
        manifest_sha256=sha256_file(published_manifest),
    )
    latest = corpus_root / "latest.json"
    temporary_latest = corpus_root / f".latest.{uuid.uuid4()}.tmp"
    try:
        temporary_latest.write_text(
            json.dumps(pointer.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_latest.replace(latest)
    finally:
        temporary_latest.unlink(missing_ok=True)
    if on_event is not None:
        on_event("latest_ready")
    return CorpusPublishReport(
        version_directory=str(destination),
        latest_path=str(latest),
        pointer=pointer,
    )


def archive_published_package(
    settings: Settings,
    version_directory: Path,
    *,
    profile: LocalProfile,
) -> CorpusArchiveReport:
    """Copy an immutable published version to a distinct administrator-protected drive."""

    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    manifest, _ = _verified_manifest(version_directory.resolve())
    configured = settings.distribution.administrator_archive_root
    if configured is None:
        raise CorpusPublishError("administrator archive drive is not configured")
    root = configured.resolve()
    synchronized = settings.distribution.synchronized_root
    if root.is_relative_to(settings.paths.data_dir.resolve()) or (
        synchronized is not None
        and (root == synchronized.resolve() or root.is_relative_to(synchronized.resolve()))
    ):
        raise CorpusPublishError(
            "administrator archive must be outside local and synchronized data"
        )
    root.mkdir(parents=True, exist_ok=True)
    destination = root / manifest.corpus_version
    if destination.exists():
        existing, _ = _verified_manifest(destination)
        if existing != manifest:
            raise CorpusPublishError("administrator archive version has different content")
    else:
        staging_root = root / f".a-{uuid.uuid4().hex[:8]}"
        staging = staging_root / manifest.corpus_version
        try:
            staging_root.mkdir()
            shutil.copytree(version_directory, staging)
            copied, _ = _verified_manifest(staging)
            if copied != manifest:
                raise CorpusPublishError("administrator archive copy differs from publication")
            staging.replace(destination)
        finally:
            if staging_root.exists():
                shutil.rmtree(staging_root)
    archived_archive = destination / manifest.archive.filename
    return CorpusArchiveReport(
        version_directory=str(destination),
        archive_sha256=sha256_file(archived_archive),
    )
