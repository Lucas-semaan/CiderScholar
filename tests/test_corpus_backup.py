from __future__ import annotations

import zipfile

import pytest

from app.services.corpus_backup import (
    CorpusBackupError,
    create_corpus_backup,
    restore_corpus_backup,
)


def test_corpus_backup_restores_the_common_corpus_with_rollback(settings, tmp_path) -> None:
    corpus_file = settings.paths.common_pdf_dir / "article.pdf"
    corpus_file.write_bytes(b"original shared content")
    backup = create_corpus_backup(settings, tmp_path / "corpus.zip")
    corpus_file.write_bytes(b"modified shared content")

    previous = restore_corpus_backup(settings, backup)

    assert corpus_file.read_bytes() == b"original shared content"
    assert previous is not None and previous.is_dir()
    with zipfile.ZipFile(backup) as archive:
        assert all(
            name == "manifest.json" or name.startswith("corpus/") for name in archive.namelist()
        )


def test_corpus_restore_rejects_path_traversal(settings, tmp_path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("../corpus/stolen.txt", "unsafe")

    with pytest.raises(CorpusBackupError, match="unsafe path"):
        restore_corpus_backup(settings, archive)

    assert not (settings.paths.common_dir / "stolen.txt").exists()
