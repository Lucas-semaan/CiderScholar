"""Deterministic, atomic builder for distributable common-corpus packages."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app import __version__
from app.config import Settings
from app.corpus_packages.hashing import sha256_file
from app.corpus_packages.identity import corpus_version_id
from app.corpus_packages.layout import common_package_files, package_relative_path
from app.corpus_packages.models import (
    ArchiveDigest,
    ArtifactDigest,
    CorpusManifest,
)
from app.corpus_packages.offline import CommonCorpusOfflineGuard
from app.corpus_packages.validation import validate_corpus_counts
from app.database.migrations import CURRENT_SCHEMA_VERSION

Clock = Callable[[], datetime]


class CorpusPackageBuildError(RuntimeError):
    """A package cannot be built or an existing immutable version is invalid."""


class CorpusPackageBuildReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_directory: str
    archive_path: str
    manifest_path: str
    reused_existing: bool
    manifest: CorpusManifest


def _kind(relative_path: str) -> str:
    top_level = relative_path.split("/", 1)[0]
    if top_level == "database":
        return "sqlite"
    if top_level == "qdrant":
        return "qdrant"
    return "metadata"


def _copy_snapshot(
    settings: Settings,
    guard: CommonCorpusOfflineGuard,
    payload_root: Path,
) -> list[Path]:
    copied: list[Path] = []
    database_destination = payload_root / "database" / settings.paths.common_database_path.name
    guard.copy_checkpointed_sqlite(database_destination)
    copied.append(database_destination)
    for source in common_package_files(settings):
        relative = package_relative_path(settings, source)
        if relative.startswith("database/"):
            continue
        destination = payload_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
    return sorted(copied, key=lambda path: path.relative_to(payload_root).as_posix())


def _artifact_digests(payload_root: Path, paths: list[Path]) -> list[ArtifactDigest]:
    return [
        ArtifactDigest(
            relative_path=path.relative_to(payload_root).as_posix(),
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
            kind=_kind(path.relative_to(payload_root).as_posix()),
        )
        for path in paths
    ]


def _write_deterministic_zip(
    archive_path: Path,
    payload_root: Path,
    artifacts: list[ArtifactDigest],
) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            source = payload_root / Path(artifact.relative_path)
            info = zipfile.ZipInfo(artifact.relative_path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def _load_existing(final_directory: Path) -> CorpusPackageBuildReport:
    manifest_path = final_directory / "manifest.json"
    if not manifest_path.is_file():
        raise CorpusPackageBuildError("immutable version directory has no manifest")
    manifest = CorpusManifest.model_validate_json(manifest_path.read_bytes())
    archive_path = final_directory / manifest.archive.filename
    if not archive_path.is_file() or sha256_file(archive_path) != manifest.archive.sha256:
        raise CorpusPackageBuildError("immutable version archive does not match its manifest")
    return CorpusPackageBuildReport(
        version_directory=str(final_directory),
        archive_path=str(archive_path),
        manifest_path=str(manifest_path),
        reused_existing=True,
        manifest=manifest,
    )


def build_corpus_package(
    settings: Settings,
    *,
    output_root: Path | None = None,
    clock: Clock | None = None,
) -> CorpusPackageBuildReport:
    """Build in a temporary directory and expose the complete immutable pair atomically."""

    root = (output_root or settings.paths.exports_dir / "corpus-packages").resolve()
    if root.is_relative_to(settings.paths.common_dir.resolve()) or root.is_relative_to(
        settings.paths.private_dir.resolve()
    ):
        raise CorpusPackageBuildError("package output cannot be inside an active corpus")
    root.mkdir(parents=True, exist_ok=True)
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise CorpusPackageBuildError("package clock must return a timezone-aware datetime")

    with tempfile.TemporaryDirectory(prefix=".build-", dir=root) as temporary:
        temporary_root = Path(temporary)
        payload_root = temporary_root / "payload"
        payload_root.mkdir()
        with CommonCorpusOfflineGuard(settings) as guard:
            counts = validate_corpus_counts(settings, guard)
            paths = _copy_snapshot(settings, guard, payload_root)
        artifacts = _artifact_digests(payload_root, paths)
        version = corpus_version_id(
            schema_version=CURRENT_SCHEMA_VERSION,
            minimum_app_version=__version__,
            counts=counts,
            artifacts=artifacts,
        )
        final_directory = root / version
        if final_directory.exists():
            return _load_existing(final_directory)

        stage = temporary_root / "version"
        stage.mkdir()
        archive_path = stage / "corpus.zip"
        _write_deterministic_zip(archive_path, payload_root, artifacts)
        archive = ArchiveDigest(
            filename=archive_path.name,
            size_bytes=archive_path.stat().st_size,
            sha256=sha256_file(archive_path),
        )
        manifest = CorpusManifest(
            corpus_version=version,
            published_at=now,
            schema_version=CURRENT_SCHEMA_VERSION,
            minimum_app_version=__version__,
            counts=counts,
            artifacts=artifacts,
            archive=archive,
        )
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        stage.replace(final_directory)
    return CorpusPackageBuildReport(
        version_directory=str(final_directory),
        archive_path=str(final_directory / archive.filename),
        manifest_path=str(final_directory / "manifest.json"),
        reused_existing=False,
        manifest=manifest,
    )
