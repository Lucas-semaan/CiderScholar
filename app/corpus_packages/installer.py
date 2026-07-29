"""Stage, validate and activate locally synchronized common-corpus updates."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict
from qdrant_client import QdrantClient

from app.config import Settings
from app.corpus_packages.hashing import sha256_bytes, sha256_file
from app.corpus_packages.models import CorpusManifest
from app.corpus_packages.signatures import (
    PackageSignatureError,
    verify_corpus_package_signatures,
)
from app.corpus_packages.updates import (
    LatestState,
    check_app_compatibility,
    compare_corpus_versions,
    read_latest_manifest,
)
from app.database.migrations import CURRENT_SCHEMA_VERSION


class CorpusInstallError(RuntimeError):
    """A corpus update cannot safely advance to the next installation phase."""


class StagedCorpusPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staging_directory: str
    archive_path: str
    manifest_path: str
    manifest: CorpusManifest


class ExtractedCorpusPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staging_directory: str
    extracted_directory: str
    manifest_path: str
    manifest: CorpusManifest


class ValidatedCorpusPackage(ExtractedCorpusPackage):
    validated: Literal[True] = True


class ReadyCorpusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str
    extracted_directory: str
    manifest_path: str
    manifest_sha256: str
    marked_at: datetime


def _remove_staging(settings: Settings, staged: StagedCorpusPackage) -> None:
    staging = Path(staged.staging_directory).resolve()
    expected_root = (settings.paths.cache_dir / "corpus-updates").resolve()
    if staging != expected_root and staging.is_relative_to(expected_root):
        shutil.rmtree(staging, ignore_errors=True)


def _atomic_stage_copy(source: Path, destination: Path, prefix: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{prefix}.{uuid.uuid4().hex[:8]}.part"
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def stage_available_package(settings: Settings) -> StagedCorpusPackage:
    """Copy an available archive locally without changing the active common corpus."""

    comparison = compare_corpus_versions(settings)
    if not comparison.download_required:
        raise CorpusInstallError(comparison.message)
    latest = read_latest_manifest(settings)
    if latest.state is not LatestState.AVAILABLE or latest.manifest is None:
        raise CorpusInstallError(latest.message)
    compatibility = check_app_compatibility(latest.manifest)
    if not compatibility.compatible:
        raise CorpusInstallError(compatibility.message)
    if latest.manifest_path is None:
        raise CorpusInstallError("latest manifest path is unavailable")
    synchronized_version = Path(latest.manifest_path).parent
    source_archive = synchronized_version / latest.manifest.archive.filename
    if not source_archive.is_file():
        raise CorpusInstallError("synchronized corpus archive is unavailable")

    staging = (
        settings.paths.cache_dir / "corpus-updates" / latest.manifest.corpus_version
    ).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / "corpus.zip"
    _atomic_stage_copy(source_archive, archive, "corpus")
    manifest_path = staging / "manifest.json"
    _atomic_stage_copy(Path(latest.manifest_path), manifest_path, "manifest")
    if settings.distribution.signature_required:
        for filename in (
            "signatures.json",
            "manifest.json.sig",
            "corpus.zip.sig",
        ):
            source = synchronized_version / filename
            if not source.is_file():
                _remove_staging(
                    settings,
                    StagedCorpusPackage(
                        staging_directory=str(staging),
                        archive_path=str(archive),
                        manifest_path=str(manifest_path),
                        manifest=latest.manifest,
                    ),
                )
                raise CorpusInstallError(f"synchronized signature is unavailable: {filename}")
            _atomic_stage_copy(source, staging / filename, filename)
    return StagedCorpusPackage(
        staging_directory=str(staging),
        archive_path=str(archive),
        manifest_path=str(manifest_path),
        manifest=latest.manifest,
    )


def verify_staged_package(
    settings: Settings,
    staged: StagedCorpusPackage,
) -> StagedCorpusPackage:
    """Verify archive and every artifact digest, deleting corrupt staging on failure."""

    archive_path = Path(staged.archive_path)
    manifest_path = Path(staged.manifest_path)
    try:
        parsed_manifest = CorpusManifest.model_validate_json(manifest_path.read_bytes())
        if parsed_manifest != staged.manifest:
            raise CorpusInstallError("staged manifest differs from synchronized metadata")
        archive_digest = sha256_file(archive_path)
        if (
            archive_path.stat().st_size != staged.manifest.archive.size_bytes
            or archive_digest != staged.manifest.archive.sha256
        ):
            raise CorpusInstallError("staged archive hash mismatch")
        if settings.distribution.signature_required:
            try:
                verify_corpus_package_signatures(
                    Path(staged.staging_directory),
                    allowed_signers=settings.distribution.allowed_signers_path,
                )
            except PackageSignatureError as error:
                raise CorpusInstallError(str(error)) from error
        expected = {artifact.relative_path: artifact for artifact in staged.manifest.artifacts}
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise CorpusInstallError("staged archive artifact list mismatch")
            for name, artifact in expected.items():
                payload = archive.read(name)
                if len(payload) != artifact.size_bytes:
                    raise CorpusInstallError(f"staged artifact size mismatch: {name}")
                if sha256_bytes(payload) != artifact.sha256:
                    raise CorpusInstallError(f"staged artifact hash mismatch: {name}")
    except (OSError, ValueError, zipfile.BadZipFile, CorpusInstallError) as exc:
        _remove_staging(settings, staged)
        if isinstance(exc, CorpusInstallError):
            raise
        raise CorpusInstallError("staged corpus package is corrupt") from exc
    return staged


def safe_archive_destination(root: Path, member_name: str) -> Path:
    posix = PurePosixPath(member_name)
    windows = PureWindowsPath(member_name)
    if (
        not member_name
        or "\\" in member_name
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
    ):
        raise CorpusInstallError(f"unsafe archive path: {member_name}")
    destination = (root / Path(*posix.parts)).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise CorpusInstallError(f"archive path escapes extraction root: {member_name}")
    return destination


def extract_staged_package(
    settings: Settings,
    staged: StagedCorpusPackage,
) -> ExtractedCorpusPackage:
    """Extract verified regular files without delegating path handling to ZipFile."""

    verify_staged_package(settings, staged)
    extraction_root = Path(staged.staging_directory) / "extracted"
    if extraction_root.exists():
        shutil.rmtree(extraction_root)
    extraction_root.mkdir()
    try:
        with zipfile.ZipFile(staged.archive_path) as archive:
            for member in archive.infolist():
                destination = safe_archive_destination(extraction_root, member.filename)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                mode = member.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:
                    raise CorpusInstallError("symbolic links are forbidden in corpus archives")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
    except Exception as exc:
        _remove_staging(settings, staged)
        if isinstance(exc, CorpusInstallError):
            raise
        raise CorpusInstallError("corpus extraction failed") from exc
    return ExtractedCorpusPackage(
        staging_directory=staged.staging_directory,
        extracted_directory=str(extraction_root),
        manifest_path=staged.manifest_path,
        manifest=staged.manifest,
    )


def _validate_staged_qdrant(
    settings: Settings,
    root: Path,
    expected_vectors: int,
    connection: sqlite3.Connection,
) -> None:
    qdrant_root = root / "qdrant"
    if not any(path.is_file() and path.name != ".lock" for path in qdrant_root.rglob("*")):
        if expected_vectors:
            raise CorpusInstallError("staged Qdrant index is missing")
        return
    client = QdrantClient(
        path=str(qdrant_root),
        force_disable_check_same_thread=True,
        cloud_inference=False,
    )
    try:
        collection = settings.qdrant.collection_name
        exists = client.collection_exists(collection)
        vectors = int(client.count(collection, exact=True).count) if exists else 0
        if vectors != expected_vectors:
            raise CorpusInstallError(
                f"staged Qdrant count mismatch: expected={expected_vectors}, actual={vectors}"
            )
        if vectors:
            points, _ = client.scroll(
                collection,
                limit=1,
                with_payload=False,
                with_vectors=False,
            )
            if not points:
                raise CorpusInstallError("staged Qdrant index cannot return a point")
            chunk_id = int(points[0].id)
            if (
                connection.execute("SELECT 1 FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
                is None
            ):
                raise CorpusInstallError("staged Qdrant point is absent from SQLite")
    finally:
        client.close()


def validate_extracted_corpus(
    settings: Settings,
    extracted: ExtractedCorpusPackage,
) -> ValidatedCorpusPackage:
    """Open and query staged SQLite/Qdrant before any activation marker is written."""

    root = Path(extracted.extracted_directory)
    database_path = root / "database" / settings.paths.common_database_path.name
    try:
        connection = sqlite3.connect(database_path)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise CorpusInstallError("staged SQLite integrity check failed")
            schema = int(
                connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            )
            if schema != extracted.manifest.schema_version or schema != CURRENT_SCHEMA_VERSION:
                raise CorpusInstallError("staged SQLite schema version is incompatible")
            articles = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
            chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            if (
                articles != extracted.manifest.counts.articles
                or chunks != extracted.manifest.counts.chunks
            ):
                raise CorpusInstallError("staged SQLite counts differ from the manifest")
            _validate_staged_qdrant(
                settings,
                root,
                extracted.manifest.counts.vectors,
                connection,
            )
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as exc:
        raise CorpusInstallError("staged SQLite database cannot be opened") from exc
    return ValidatedCorpusPackage(
        **extracted.model_dump(mode="python", exclude={"validated"}),
        validated=True,
    )


def ready_update_path(settings: Settings) -> Path:
    return settings.paths.cache_dir / "corpus-updates" / "ready.json"


def mark_update_ready(
    settings: Settings,
    validated: ValidatedCorpusPackage,
) -> ReadyCorpusUpdate:
    """Persist a restart-only activation marker without changing the active common path."""

    extracted = Path(validated.extracted_directory).resolve()
    expected_root = (settings.paths.cache_dir / "corpus-updates").resolve()
    if not extracted.is_relative_to(expected_root):
        raise CorpusInstallError("validated corpus is outside the update staging root")
    manifest_path = Path(validated.manifest_path)
    marker = ReadyCorpusUpdate(
        corpus_version=validated.manifest.corpus_version,
        extracted_directory=str(extracted),
        manifest_path=str(manifest_path.resolve()),
        manifest_sha256=sha256_file(manifest_path),
        marked_at=datetime.now(UTC),
    )
    destination = ready_update_path(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".ready.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(marker.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return marker
