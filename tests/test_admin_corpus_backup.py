from __future__ import annotations

import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.admin.corpus_backup import create_maintenance_backup, rollback_maintenance_backup
from app.corpus_packages.installer import CorpusInstallError
from app.database.sqlite import Database


def test_premaintenance_backup_is_protected_openable_and_rolls_back_common_only(
    settings,
    tmp_path,
) -> None:
    settings.distribution.administrator_archive_root = tmp_path / "protected"
    Database(settings.paths.common_database_path).initialize()
    marker = settings.paths.common_pdf_dir / "before.pdf"
    marker.write_bytes(b"before maintenance")
    maintenance_id = uuid4()

    backup = create_maintenance_backup(settings, maintenance_id)

    assert Path(backup.protected_directory).is_dir()
    assert len(backup.archive_sha256) == 64
    marker.write_bytes(b"defective maintenance")
    rollback_maintenance_backup(settings, maintenance_id, backup)

    assert marker.read_bytes() == b"before maintenance"


def test_rollback_streams_archive_and_artifacts(settings, tmp_path, monkeypatch) -> None:
    settings.distribution.administrator_archive_root = tmp_path / "protected"
    Database(settings.paths.common_database_path).initialize()
    marker = settings.paths.common_pdf_dir / "large.pdf"
    original = b"verbatim original document" * 100_000
    marker.write_bytes(original)
    maintenance_id = uuid4()
    backup = create_maintenance_backup(settings, maintenance_id)
    marker.write_bytes(b"defective maintenance")
    read_bytes = Path.read_bytes

    def bounded_file_read(path):
        assert path.suffix not in {".zip", ".pdf"}, "large files must be streamed"
        return read_bytes(path)

    def forbid_archive_read(*args, **kwargs):
        raise AssertionError("archive members must be streamed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", bounded_file_read)
        patch.setattr(zipfile.ZipFile, "read", forbid_archive_read)
        rollback_maintenance_backup(settings, maintenance_id, backup)

    assert marker.read_bytes() == original


@pytest.mark.parametrize("corruption", ["archive_digest", "artifact_digest", "artifact_size"])
def test_invalid_backup_never_activates_or_replaces_the_corpus(
    settings, tmp_path, monkeypatch, corruption
) -> None:
    settings.distribution.administrator_archive_root = tmp_path / "protected"
    Database(settings.paths.common_database_path).initialize()
    marker = settings.paths.common_pdf_dir / "before.pdf"
    marker.write_bytes(b"original document")
    maintenance_id = uuid4()
    backup = create_maintenance_backup(settings, maintenance_id)
    if corruption == "archive_digest":
        backup = backup.model_copy(update={"archive_sha256": "0" * 64})
    else:
        manifest_path = Path(backup.version_directory) / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = manifest["artifacts"][0]
        if corruption == "artifact_digest":
            artifact["sha256"] = "0" * 64
        else:
            artifact["size_bytes"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def forbidden_activation(*args, **kwargs):
        pytest.fail("invalid backup must never reach corpus activation")

    monkeypatch.setattr(
        "app.admin.corpus_backup.activate_prepared_common_corpus", forbidden_activation
    )
    with pytest.raises(CorpusInstallError):
        rollback_maintenance_backup(settings, maintenance_id, backup)

    assert marker.read_bytes() == b"original document"
    assert not list((settings.paths.data_dir / ".r").glob("*/x-*"))
