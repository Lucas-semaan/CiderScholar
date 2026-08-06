from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.admin.corpus_backup import create_maintenance_backup, rollback_maintenance_backup
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
