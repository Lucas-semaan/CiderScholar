from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.corpora import LocalProfile
from app.corpus_packages import validation
from app.corpus_packages.builder import CorpusPackageBuildReport, build_corpus_package
from app.corpus_packages.distribution import (
    DistributionPathError,
    create_distribution_layout,
    validate_distribution_root,
)
from app.corpus_packages.hashing import sha256_file
from app.corpus_packages.identity import corpus_version_id
from app.corpus_packages.installer import stage_available_package
from app.corpus_packages.layout import common_package_files, package_relative_path
from app.corpus_packages.models import (
    ArchiveDigest,
    ArtifactDigest,
    CorpusCounts,
    CorpusManifest,
)
from app.corpus_packages.offline import CommonCorpusOfflineGuard
from app.corpus_packages.publisher import archive_published_package, publish_corpus_package
from app.corpus_packages.updates import (
    InstalledCorpusState,
    LatestState,
    check_app_compatibility,
    compare_corpus_versions,
    read_latest_manifest,
    write_installed_state,
)
from app.corpus_packages.validation import (
    CorpusCountValidationError,
    validate_corpus_counts,
)
from app.database.sqlite import Database
from app.resource_lock import ResourceBusyError, ResourceFileLock
from app.services.corpus_updates import directory_hashes
from scripts import build_corpus_package as build_command


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sha256_file_streams_large_payload_without_changing_digest(tmp_path) -> None:
    payload = (b"cider" * 200_000) + b"-package"
    source = tmp_path / "large-corpus-artifact.bin"
    source.write_bytes(payload)

    assert sha256_file(source) == hashlib.sha256(payload).hexdigest()


def _publishable_version(root: Path) -> tuple[Path, CorpusManifest]:
    directory = root / f"corpus-v1-{'a' * 64}"
    directory.mkdir(parents=True)
    archive = directory / "corpus.zip"
    archive.write_bytes(b"verified archive")
    manifest = _manifest().model_copy(
        update={
            "archive": ArchiveDigest(
                filename=archive.name,
                size_bytes=archive.stat().st_size,
                sha256=_file_sha256(archive),
            )
        }
    )
    (directory / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return directory, manifest


def _manifest() -> CorpusManifest:
    version = f"corpus-v1-{'a' * 64}"
    return CorpusManifest(
        corpus_version=version,
        published_at=datetime(2026, 7, 22, tzinfo=UTC),
        schema_version=13,
        minimum_app_version="0.1.0",
        counts=CorpusCounts(articles=10, chunks=100, vectors=100),
        artifacts=[
            ArtifactDigest(
                relative_path="database/science_rag.sqlite3",
                size_bytes=4096,
                sha256="b" * 64,
                kind="sqlite",
            )
        ],
        archive=ArchiveDigest(
            filename="corpus.zip",
            size_bytes=1024,
            sha256="c" * 64,
        ),
    )


def test_corpus_manifest_contains_version_date_schema_compatibility_and_hashes() -> None:
    manifest = _manifest()
    payload = manifest.model_dump(mode="json")

    assert payload["format_version"] == 1
    assert payload["corpus_version"].startswith("corpus-v1-")
    assert payload["published_at"].endswith("Z")
    assert payload["schema_version"] == 13
    assert payload["minimum_app_version"] == "0.1.0"
    assert payload["artifacts"][0]["sha256"] == "b" * 64
    assert payload["archive"]["sha256"] == "c" * 64


@pytest.mark.parametrize("path", ["../private/file", "/absolute/file", "private\\file"])
def test_corpus_manifest_rejects_unsafe_artifact_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="safe POSIX relative path"):
        ArtifactDigest(relative_path=path, size_bytes=1, sha256="d" * 64, kind="metadata")


def test_corpus_manifest_rejects_duplicate_artifacts() -> None:
    manifest = _manifest().model_dump(mode="python")
    manifest["artifacts"].append(manifest["artifacts"][0])

    with pytest.raises(ValidationError, match="must be unique"):
        CorpusManifest.model_validate(manifest)


def test_corpus_version_is_content_addressed_and_order_independent() -> None:
    first = ArtifactDigest(
        relative_path="database.sqlite3",
        size_bytes=10,
        sha256="1" * 64,
        kind="sqlite",
    )
    second = ArtifactDigest(
        relative_path="qdrant/index.bin",
        size_bytes=20,
        sha256="2" * 64,
        kind="qdrant",
    )
    counts = CorpusCounts(articles=1, chunks=2, vectors=2)
    options = {
        "schema_version": 13,
        "minimum_app_version": "0.1.0",
        "counts": counts,
    }

    version = corpus_version_id(**options, artifacts=[first, second])
    reordered = corpus_version_id(**options, artifacts=[second, first])
    changed = corpus_version_id(
        **options,
        artifacts=[first, second.model_copy(update={"sha256": "3" * 64})],
    )

    assert version == reordered
    assert version != changed
    assert version == f"corpus-v1-{version.removeprefix('corpus-v1-')}"


def test_package_layout_includes_only_common_database_pdf_and_qdrant(settings) -> None:
    expected = [
        settings.paths.common_database_path,
        settings.paths.common_pdf_dir / "article.pdf",
        settings.paths.common_qdrant_dir / "collection" / "index.bin",
    ]
    for path in expected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode("utf-8"))
    (settings.paths.common_extracted_dir / "cache.json").write_text("cache", encoding="utf-8")
    (settings.paths.common_qdrant_dir / "runtime.lock").write_text("lock", encoding="utf-8")
    (settings.paths.private_pdf_dir / "private.pdf").write_bytes(b"private")
    (settings.paths.data_dir / "config.yaml").write_text("secret", encoding="utf-8")

    selected = common_package_files(settings)

    assert selected == expected
    assert [package_relative_path(settings, path) for path in selected] == [
        "database/science_rag.sqlite3",
        "pdf/article.pdf",
        "qdrant/collection/index.bin",
    ]
    assert all(not path.is_relative_to(settings.paths.private_dir) for path in selected)


def test_package_guard_requires_qdrant_to_be_closed(settings) -> None:
    Database(settings.paths.common_database_path).initialize()
    runtime_lock = ResourceFileLock(settings.paths.common_dir / ".runtime.lock")
    runtime_lock.acquire()
    try:
        with (
            pytest.raises(ResourceBusyError, match="already open"),
            CommonCorpusOfflineGuard(settings),
        ):
            raise AssertionError("busy corpus must not enter the package guard")
    finally:
        runtime_lock.release()

    with CommonCorpusOfflineGuard(settings) as guard:
        assert guard.connection is not None


def test_sqlite_package_copy_is_checkpointed_and_opens_without_wal(settings, tmp_path) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    with database.connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            "INSERT INTO articles (id, sha256, title, pdf_path) VALUES (?, ?, ?, ?)",
            ("article-1", "e" * 64, "Article", "article.pdf"),
        )
        connection.commit()
    copied = tmp_path / "database" / "science_rag.sqlite3"

    with CommonCorpusOfflineGuard(settings) as guard:
        guard.copy_checkpointed_sqlite(copied)

    assert not copied.with_name(f"{copied.name}-wal").exists()
    with sqlite3.connect(copied) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    source_wal = settings.paths.common_database_path.with_name(
        f"{settings.paths.common_database_path.name}-wal"
    )
    assert not source_wal.exists() or source_wal.stat().st_size == 0


def test_package_count_mismatch_blocks_publication(settings, monkeypatch) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "f" * 64,
            "title": "Article",
            "authors": [],
            "pdf_path": "article.pdf",
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Evidence",
                "token_count": 1,
            }
        ],
    )
    vector_count = [0]
    monkeypatch.setattr(validation, "_vector_count", lambda _settings: vector_count[0])

    with CommonCorpusOfflineGuard(settings) as guard:
        with pytest.raises(CorpusCountValidationError, match="chunks=1, indexed=0, vectors=0"):
            validate_corpus_counts(settings, guard)
        guard.connection.execute("UPDATE chunks SET embedding_status = 'indexed'")
        vector_count[0] = 1
        counts = validate_corpus_counts(settings, guard)

    assert counts == CorpusCounts(articles=1, chunks=1, vectors=1)


def test_package_is_built_in_temporary_storage_and_published_as_complete_pair(
    settings, tmp_path, monkeypatch
) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    pdf = settings.paths.common_pdf_dir / "article.pdf"
    pdf.write_bytes(b"%PDF-1.4 packaged")
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "9" * 64,
            "doi": "10.1000/package",
            "title": "Packaged article",
            "authors": [],
            "pdf_path": str(pdf),
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Packaged evidence",
                "token_count": 2,
                "embedding_status": "indexed",
            }
        ],
    )
    qdrant_file = settings.paths.common_qdrant_dir / "collection" / "vectors.bin"
    qdrant_file.parent.mkdir(parents=True)
    qdrant_file.write_bytes(b"vector payload")
    monkeypatch.setattr(validation, "_vector_count", lambda _settings: 1)
    output = tmp_path / "packages"
    published_at = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)

    report = build_corpus_package(
        settings,
        output_root=output,
        clock=lambda: published_at,
    )
    version_directory = Path(report.version_directory)
    archive_path = Path(report.archive_path)
    manifest_path = Path(report.manifest_path)

    assert report.reused_existing is False
    assert version_directory.parent == output
    assert archive_path.is_file() and manifest_path.is_file()
    assert not list(output.glob(".build-*"))
    assert report.manifest.archive.sha256 == _file_sha256(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            artifact.relative_path for artifact in report.manifest.artifacts
        ]
        assert "database/science_rag.sqlite3" in archive.namelist()
        assert "pdf/article.pdf" in archive.namelist()
        assert "qdrant/collection/vectors.bin" in archive.namelist()

    rebuilt = build_corpus_package(
        settings,
        output_root=tmp_path / "packages-second",
        clock=lambda: datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
    )
    assert rebuilt.manifest.corpus_version == report.manifest.corpus_version
    assert rebuilt.manifest.archive.sha256 == report.manifest.archive.sha256
    assert rebuilt.manifest.artifacts == report.manifest.artifacts
    assert rebuilt.manifest.published_at != report.manifest.published_at


def test_admin_build_command_writes_one_strict_json_document(
    settings, tmp_path, monkeypatch, capsys
) -> None:
    manifest = _manifest()
    report = {
        "version_directory": str(tmp_path / manifest.corpus_version),
        "archive_path": str(tmp_path / manifest.archive.filename),
        "manifest_path": str(tmp_path / "manifest.json"),
        "reused_existing": False,
        "manifest": manifest,
    }
    monkeypatch.setattr(build_command, "load_settings", lambda: settings)
    monkeypatch.setattr(build_command, "load_local_profile", lambda: LocalProfile.ADMIN)
    monkeypatch.setattr(
        build_command,
        "build_corpus_package",
        lambda _settings, *, output_root: CorpusPackageBuildReport.model_validate(report),
    )

    result = build_command.main(["--output", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["manifest"]["corpus_version"] == manifest.corpus_version
    assert payload["reused_existing"] is False


def test_sharepoint_layout_contains_only_distribution_directories(tmp_path) -> None:
    paths = create_distribution_layout(tmp_path / "CiderScholar")

    assert paths.installers.is_dir()
    assert paths.corpus.is_dir()
    assert paths.suggestions_inbox.is_dir()
    assert paths.archive.is_dir()
    assert {path.relative_to(paths.root).as_posix() for path in paths.root.rglob("*")} == {
        "archive",
        "corpus",
        "installers",
        "suggestions",
        "suggestions/inbox",
    }
    assert not any(path.is_file() for path in paths.root.rglob("*"))


def test_distribution_root_requires_expected_name_or_explicit_confirmation(
    settings, tmp_path
) -> None:
    expected = tmp_path / "OneDrive" / "CiderScholar"
    expected.mkdir(parents=True)
    settings.distribution.enabled = True
    settings.distribution.synchronized_root = expected

    assert validate_distribution_root(settings) == expected.resolve()
    arbitrary = tmp_path / "unverified-share"
    arbitrary.mkdir()
    settings.distribution.synchronized_root = arbitrary
    with pytest.raises(DistributionPathError, match="explicit confirmation"):
        validate_distribution_root(settings)
    assert validate_distribution_root(settings, explicit_confirmation=True) == arbitrary.resolve()

    settings.distribution.synchronized_root = settings.paths.private_dir
    with pytest.raises(DistributionPathError, match="local application data"):
        validate_distribution_root(settings, explicit_confirmation=True)


def test_publication_exposes_version_before_latest_pointer(settings, tmp_path) -> None:
    source, manifest = _publishable_version(tmp_path / "build")
    synchronized = tmp_path / "OneDrive" / "CiderScholar"
    synchronized.mkdir(parents=True)
    settings.distribution.enabled = True
    settings.distribution.synchronized_root = synchronized
    events: list[str] = []

    def record(event: str) -> None:
        if event == "version_ready":
            assert not (synchronized / "corpus" / "latest.json").exists()
        events.append(event)

    report = publish_corpus_package(
        settings,
        source,
        profile=LocalProfile.ADMIN,
        on_event=record,
    )

    assert events == ["version_ready", "latest_ready"]
    assert Path(report.version_directory).is_dir()
    assert Path(report.latest_path).is_file()
    assert report.pointer.corpus_version == manifest.corpus_version
    assert report.pointer.manifest_sha256 == _file_sha256(
        Path(report.version_directory) / "manifest.json"
    )

    protected = tmp_path / "administrator-protected-drive"
    settings.distribution.administrator_archive_root = protected
    archived = archive_published_package(
        settings,
        Path(report.version_directory),
        profile=LocalProfile.ADMIN,
    )
    assert Path(archived.version_directory).parent == protected
    assert archived.archive_sha256 == manifest.archive.sha256
    assert _file_sha256(Path(archived.version_directory) / "corpus.zip") == (
        _file_sha256(Path(report.version_directory) / "corpus.zip")
    )


def test_latest_reader_distinguishes_missing_sync_from_missing_pointer(settings, tmp_path) -> None:
    settings.distribution.enabled = True
    settings.distribution.synchronized_root = tmp_path / "not-synchronized"

    missing_sync = read_latest_manifest(settings)
    settings.distribution.synchronized_root.mkdir()
    missing_latest = read_latest_manifest(settings)

    assert missing_sync.state is LatestState.SYNC_UNAVAILABLE
    assert "synchronisé" in missing_sync.message
    assert missing_latest.state is LatestState.LATEST_UNAVAILABLE


def test_identical_installed_and_available_versions_require_no_download(settings, tmp_path) -> None:
    source, manifest = _publishable_version(tmp_path / "build")
    synchronized = tmp_path / "OneDrive" / "CiderScholar"
    synchronized.mkdir(parents=True)
    settings.distribution.enabled = True
    settings.distribution.synchronized_root = synchronized
    publish_corpus_package(settings, source, profile=LocalProfile.ADMIN)
    write_installed_state(
        settings,
        InstalledCorpusState(
            corpus_version=manifest.corpus_version,
            installed_at=datetime.now(UTC),
            manifest_sha256="0" * 64,
        ),
    )

    comparison = compare_corpus_versions(settings)

    assert comparison.latest_state is LatestState.AVAILABLE
    assert comparison.update_available is False
    assert comparison.download_required is False
    assert comparison.installed_version == comparison.available_version


def test_application_too_old_explains_the_update_block() -> None:
    manifest = _manifest().model_copy(update={"minimum_app_version": "2.0.0"})

    compatibility = check_app_compatibility(manifest, current_app_version="1.9.9")

    assert compatibility.compatible is False
    assert compatibility.minimum_app_version == "2.0.0"
    assert "version minimale 2.0.0" in compatibility.message
    assert "version installée 1.9.9" in compatibility.message


def test_staging_copy_leaves_active_common_corpus_unchanged(settings, tmp_path) -> None:
    active = settings.paths.common_dir / "active.bin"
    active.write_bytes(b"active corpus remains available")
    before = directory_hashes(settings.paths.common_dir)
    source, manifest = _publishable_version(tmp_path / "build")
    synchronized = tmp_path / "OneDrive" / "CiderScholar"
    synchronized.mkdir(parents=True)
    settings.distribution.enabled = True
    settings.distribution.synchronized_root = synchronized
    publish_corpus_package(settings, source, profile=LocalProfile.ADMIN)

    staged = stage_available_package(settings)

    assert staged.manifest.corpus_version == manifest.corpus_version
    assert Path(staged.archive_path).read_bytes() == (source / "corpus.zip").read_bytes()
    assert Path(staged.manifest_path).is_file()
    assert directory_hashes(settings.paths.common_dir) == before
