from __future__ import annotations

import zipfile

import pytest

from app.services.corpus_updates import directory_hashes
from app.services.private_backup import (
    PrivateBackupError,
    create_private_backup,
    restore_private_backup,
)


def test_private_backup_restore_never_changes_common_corpus(settings, tmp_path) -> None:
    common_file = settings.paths.common_dir / "common.bin"
    private_file = settings.paths.private_pdf_dir / "private.pdf"
    common_file.write_bytes(b"shared common content")
    private_file.write_bytes(b"private original content")
    common_before = directory_hashes(settings.paths.common_dir)
    backup = create_private_backup(settings, tmp_path / "private.zip")
    private_file.write_bytes(b"private modified content")

    previous = restore_private_backup(settings, backup)

    assert private_file.read_bytes() == b"private original content"
    assert directory_hashes(settings.paths.common_dir) == common_before
    assert previous is not None and previous.is_dir()
    with zipfile.ZipFile(backup) as archive:
        assert all(
            name == "manifest.json" or name.startswith("private/") for name in archive.namelist()
        )
        assert not any("common" in name for name in archive.namelist())


def test_private_restore_rejects_path_traversal(settings, tmp_path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("../common/stolen.txt", "unsafe")

    with pytest.raises(PrivateBackupError, match="unsafe path"):
        restore_private_backup(settings, archive)

    assert not (settings.paths.common_dir / "stolen.txt").exists()
