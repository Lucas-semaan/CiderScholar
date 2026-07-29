from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.corpus_packages.actions import (
    load_validated_update,
    save_validated_update,
    validated_update_path,
)
from app.corpus_packages.activation import (
    activate_ready_update_at_startup,
    apply_scheduled_rollback_at_startup,
    rollback_marker_path,
    rollback_previous_at_startup,
    schedule_previous_rollback,
)
from app.corpus_packages.installer import (
    CorpusInstallError,
    StagedCorpusPackage,
    _atomic_stage_copy,
    extract_staged_package,
    mark_update_ready,
    ready_update_path,
    safe_archive_destination,
    validate_extracted_corpus,
    verify_staged_package,
)
from app.corpus_packages.models import (
    ArchiveDigest,
    ArtifactDigest,
    CorpusCounts,
    CorpusManifest,
)
from app.corpus_packages.updates import read_installed_state
from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database
from app.services.corpus_updates import directory_hashes


def _staged_package(settings, payload: bytes = b"SQLite snapshot") -> StagedCorpusPackage:
    version = f"corpus-v1-{'a' * 64}"
    staging = settings.paths.cache_dir / "corpus-updates" / version
    staging.mkdir(parents=True)
    artifact = ArtifactDigest(
        relative_path="database/science_rag.sqlite3",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        kind="sqlite",
    )
    archive_path = staging / "corpus.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(artifact.relative_path, payload)
    manifest = CorpusManifest(
        corpus_version=version,
        published_at=datetime(2026, 7, 22, tzinfo=UTC),
        schema_version=CURRENT_SCHEMA_VERSION,
        minimum_app_version="0.1.0",
        counts=CorpusCounts(articles=0, chunks=0, vectors=0),
        artifacts=[artifact],
        archive=ArchiveDigest(
            size_bytes=archive_path.stat().st_size,
            sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        ),
    )
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return StagedCorpusPackage(
        staging_directory=str(staging),
        archive_path=str(archive_path),
        manifest_path=str(manifest_path),
        manifest=manifest,
    )


def test_every_staged_hash_is_checked_before_extraction(settings) -> None:
    staged = _staged_package(settings)

    assert verify_staged_package(settings, staged) == staged
    Path(staged.archive_path).write_bytes(b"corrupt")

    with pytest.raises(CorpusInstallError, match="hash mismatch"):
        verify_staged_package(settings, staged)

    assert not Path(staged.staging_directory).exists()


@pytest.mark.parametrize(
    "member",
    ["../private/file", "/absolute/file", "C:/Windows/file", "folder\\file"],
)
def test_extraction_rejects_paths_outside_staging(tmp_path, member: str) -> None:
    with pytest.raises(CorpusInstallError, match="unsafe archive path"):
        safe_archive_destination(tmp_path, member)


def test_verified_archive_extracts_only_below_staging(settings) -> None:
    staged = _staged_package(settings)

    extracted = extract_staged_package(settings, staged)
    extraction_root = Path(extracted.extracted_directory)

    assert (extraction_root / "database" / "science_rag.sqlite3").read_bytes() == (
        b"SQLite snapshot"
    )
    assert all(
        path.resolve().is_relative_to(extraction_root) for path in extraction_root.rglob("*")
    )


def test_staged_sqlite_must_open_and_match_manifest_before_activation(settings) -> None:
    extracted = extract_staged_package(settings, _staged_package(settings))

    with pytest.raises(CorpusInstallError, match="SQLite database cannot be opened"):
        validate_extracted_corpus(settings, extracted)


def test_valid_empty_sqlite_and_qdrant_staging_is_searchable(settings) -> None:
    source = settings.paths.cache_dir / "empty-source.sqlite3"
    Database(source).initialize()
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    staged = _staged_package(settings, source.read_bytes())
    extracted = extract_staged_package(settings, staged)

    validated = validate_extracted_corpus(settings, extracted)

    assert validated.validated is True


def test_downloaded_validation_marker_is_rechecked_before_ready(settings) -> None:
    source = settings.paths.cache_dir / "empty-source.sqlite3"
    Database(source).initialize()
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    extracted = extract_staged_package(settings, _staged_package(settings, source.read_bytes()))
    validated = validate_extracted_corpus(settings, extracted)

    save_validated_update(settings, validated)

    assert validated_update_path(settings).is_file()
    assert load_validated_update(settings).manifest == validated.manifest


def test_rollback_is_scheduled_without_hot_swap_and_applied_at_startup(settings) -> None:
    active = settings.paths.common_dir / "active.txt"
    active.write_text("current", encoding="utf-8")
    previous = settings.paths.data_dir / "common-archive" / "common-previous"
    previous.mkdir(parents=True)
    (previous / "active.txt").write_text("previous", encoding="utf-8")

    scheduled = schedule_previous_rollback(settings)

    assert Path(scheduled.previous_path) == previous
    assert rollback_marker_path(settings).is_file()
    assert active.read_text(encoding="utf-8") == "current"

    report = apply_scheduled_rollback_at_startup(settings)

    assert report is not None
    assert active.read_text(encoding="utf-8") == "previous"
    assert not rollback_marker_path(settings).exists()


def test_validated_update_is_marked_for_restart_without_hot_activation(settings) -> None:
    active = settings.paths.common_dir / "active.txt"
    active.write_text("still active", encoding="utf-8")
    private = settings.paths.private_pdf_dir / "private.pdf"
    private.write_bytes(b"private content")
    private_before = directory_hashes(settings.paths.private_dir)
    source = settings.paths.cache_dir / "empty-source.sqlite3"
    Database(source).initialize()
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    extracted = extract_staged_package(settings, _staged_package(settings, source.read_bytes()))
    validated = validate_extracted_corpus(settings, extracted)

    ready = mark_update_ready(settings, validated)

    assert ready.corpus_version == validated.manifest.corpus_version
    assert ready_update_path(settings).is_file()
    assert active.read_text(encoding="utf-8") == "still active"

    activation = activate_ready_update_at_startup(settings)

    assert activation is not None
    assert activation.corpus_version == validated.manifest.corpus_version
    assert Path(activation.active_path) == settings.paths.common_dir
    assert activation.previous_path is not None
    assert (Path(activation.previous_path) / "active.txt").read_text(encoding="utf-8") == (
        "still active"
    )
    assert not ready_update_path(settings).exists()
    assert read_installed_state(settings).corpus_version == activation.corpus_version
    assert directory_hashes(settings.paths.private_dir) == private_before

    rolled_back = rollback_previous_at_startup(settings, Path(activation.previous_path))

    assert rolled_back.corpus_version == "unversioned"
    assert (settings.paths.common_dir / "active.txt").read_text(encoding="utf-8") == (
        "still active"
    )
    assert rolled_back.previous_path is not None
    assert Path(rolled_back.previous_path).is_dir()
    assert directory_hashes(settings.paths.private_dir) == private_before


def test_interrupted_copy_and_extraction_leave_active_corpus_valid(
    settings, tmp_path, monkeypatch
) -> None:
    active = settings.paths.common_dir / "active.txt"
    active.write_text("valid active", encoding="utf-8")
    source = tmp_path / "source.zip"
    source.write_bytes(b"source")
    destination = settings.paths.cache_dir / "corpus-updates" / "copy" / "corpus.zip"
    monkeypatch.setattr(
        "app.corpus_packages.installer.shutil.copy2",
        lambda *_args: (_ for _ in ()).throw(OSError("interrupted")),
    )

    with pytest.raises(OSError, match="interrupted"):
        _atomic_stage_copy(source, destination, "corpus")

    assert not destination.exists()
    assert not list(destination.parent.glob(".*.part"))
    assert active.read_text(encoding="utf-8") == "valid active"

    monkeypatch.undo()
    staged = _staged_package(settings)
    monkeypatch.setattr(
        "app.corpus_packages.installer.shutil.copyfileobj",
        lambda *_args: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(CorpusInstallError, match="extraction failed"):
        extract_staged_package(settings, staged)
    assert not Path(staged.staging_directory).exists()
    assert active.read_text(encoding="utf-8") == "valid active"


def test_interrupted_activation_restores_previous_common(settings, monkeypatch) -> None:
    active = settings.paths.common_dir / "active.txt"
    active.write_text("valid active", encoding="utf-8")
    source = settings.paths.cache_dir / "empty-source.sqlite3"
    Database(source).initialize()
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    extracted = extract_staged_package(settings, _staged_package(settings, source.read_bytes()))
    validated = validate_extracted_corpus(settings, extracted)
    mark_update_ready(settings, validated)
    extracted_path = Path(validated.extracted_directory).resolve()
    original_replace = Path.replace

    def interrupt_prepared_replace(path: Path, target: Path) -> Path:
        if path.resolve() == extracted_path:
            raise OSError("activation interrupted")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", interrupt_prepared_replace)

    with pytest.raises(CorpusInstallError, match="activation metadata is invalid"):
        activate_ready_update_at_startup(settings)

    assert active.read_text(encoding="utf-8") == "valid active"
    assert ready_update_path(settings).is_file()
