from __future__ import annotations

from app.database.sqlite import Database
from app.services.legacy_vector_merge import _source_chunk_offset


def _save_article(database: Database, article_id: str, sha256: str, text: str) -> None:
    database.save_article_and_chunks(
        {
            "id": article_id,
            "sha256": sha256,
            "title": article_id,
            "authors": [],
            "pdf_path": f"{article_id}.pdf",
        },
        [
            {
                "section": "Results",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": text,
                "token_count": 1,
            }
        ],
    )


def test_source_chunk_offset_recovers_the_sqlite_merge_remapping(settings, tmp_path) -> None:
    target = Database(settings.paths.common_database_path)
    target.initialize()
    for index in range(3):
        _save_article(target, f"existing-{index}", f"{index + 1:064x}", f"existing {index}")
    _save_article(target, "migrated", "a" * 64, "malo-lactic evidence")

    source = Database(tmp_path / "legacy.sqlite3")
    source.initialize()
    _save_article(source, "migrated", "a" * 64, "malo-lactic evidence")

    assert _source_chunk_offset(target.path, source.path) == 3


def test_source_chunk_offset_returns_none_without_matching_chunks(settings, tmp_path) -> None:
    target = Database(settings.paths.common_database_path)
    target.initialize()
    source = Database(tmp_path / "legacy.sqlite3")
    source.initialize()
    _save_article(source, "missing", "b" * 64, "absent evidence")

    assert _source_chunk_offset(target.path, source.path) is None
