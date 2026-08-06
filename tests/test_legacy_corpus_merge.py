from __future__ import annotations

from app.database.sqlite import Database
from app.services.legacy_corpus_merge import merge_legacy_split_corpus


def test_legacy_split_corpus_merge_copies_articles_chunks_and_managed_pdfs(settings) -> None:
    legacy_root = settings.paths.data_dir / "private"
    source = Database(legacy_root / "database" / "science_rag.sqlite3")
    source.initialize()
    pdf = legacy_root / "pdf" / "source.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 source")
    source.save_article_and_chunks(
        {
            "id": "former-private-article",
            "sha256": "a" * 64,
            "doi": "10.1000/former-private",
            "title": "Former private article",
            "authors": [],
            "pdf_path": str(pdf),
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "malo-lactic fermentation evidence",
                "token_count": 4,
            }
        ],
    )

    report = merge_legacy_split_corpus(settings)
    target = Database(settings.paths.common_database_path)

    assert report.source_articles == 1
    assert report.imported_articles == 1
    assert report.imported_chunks == 1
    assert report.pending_vector_chunks == 1
    assert target.article_chunk_ids("former-private-article")
    article = target.list_articles()[0]
    assert article["pdf_path"].startswith(str(settings.paths.common_pdf_dir))
    assert (settings.paths.data_dir / "backups").is_dir()


def test_legacy_split_corpus_merge_is_repeatable_without_duplicate_articles(settings) -> None:
    legacy_root = settings.paths.data_dir / "private"
    source = Database(legacy_root / "database" / "science_rag.sqlite3")
    source.initialize()
    source.save_article_and_chunks(
        {
            "id": "former-private-article",
            "sha256": "b" * 64,
            "title": "Former private article",
            "authors": [],
            "pdf_path": str(settings.paths.data_dir.parent / "outside-managed.pdf"),
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "evidence",
                "token_count": 1,
            }
        ],
    )

    first = merge_legacy_split_corpus(settings)
    second = merge_legacy_split_corpus(settings)

    assert first.imported_articles == 1
    assert second.imported_articles == 0
    assert second.deduplicated_articles == 1
    assert len(Database(settings.paths.common_database_path).list_articles()) == 1


def test_legacy_merge_also_consolidates_the_former_application_corpus(settings) -> None:
    source = Database(settings.paths.database_path)
    source.initialize()
    pdf = settings.paths.pdf_dir / "legacy.pdf"
    pdf.write_bytes(b"%PDF-1.4 legacy")
    source.save_article_and_chunks(
        {
            "id": "legacy-article",
            "sha256": "c" * 64,
            "title": "Legacy article",
            "authors": [],
            "pdf_path": str(pdf),
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "legacy evidence",
                "token_count": 2,
            }
        ],
    )

    report = merge_legacy_split_corpus(settings)
    target = Database(settings.paths.common_database_path)

    assert report.source_articles == 1
    assert report.imported_articles == 1
    assert target.article_chunk_ids("legacy-article")
