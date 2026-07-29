from __future__ import annotations

import hashlib

import pytest

from app.corpora import (
    CorpusMutationForbiddenError,
    CorpusScope,
    LocalProfile,
    settings_for_corpus,
)
from app.database.sqlite import Database
from app.services import workflows
from app.services.chatbot import chatbot_sources
from app.services.corpus_migration import migrate_legacy_corpus
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import index_bibliographic_abstracts


def _seed_legacy_corpus(settings, tmp_path) -> tuple[Database, str]:
    database = Database(settings.paths.database_path)
    database.initialize()
    pdf = tmp_path / "legacy.pdf"
    pdf.write_bytes(b"%PDF-1.4 legacy")
    doi = "10.1000/preserved"
    database.save_article_and_chunks(
        {
            "id": "legacy-article",
            "sha256": "a" * 64,
            "doi": doi,
            "title": "Legacy article",
            "authors": ["Test Author"],
            "pdf_path": str(pdf),
        },
        [
            {
                "section": "Results",
                "subsection": None,
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": "A preserved chunk.",
                "token_count": 4,
            }
        ],
    )
    return database, doi


def _seed_legacy_abstract(
    database: Database,
    *,
    record_id: str,
    doi: str | None,
    title: str,
    abstract: str,
    relevance_status: str = "accepted",
) -> None:
    content_hash = hashlib.sha256(f"{title}\n{abstract}".encode()).hexdigest()
    canonical_key = f"doi:{doi}" if doi else f"title:{record_id}"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors,
                content_hash, embedding_status, relevance_status
            ) VALUES (?, ?, ?, ?, ?, '["Legacy Author"]', ?, 'indexed', ?)
            """,
            (
                record_id,
                canonical_key,
                doi,
                title,
                abstract,
                content_hash,
                relevance_status,
            ),
        )
        connection.execute(
            """
            INSERT INTO bibliographic_record_sources (record_id, source, source_id)
            VALUES (?, 'OpenAlex', ?)
            """,
            (record_id, f"source-{record_id}"),
        )


def test_legacy_migration_preserves_article_count_doi_and_chunk_ids(settings, tmp_path) -> None:
    legacy, doi = _seed_legacy_corpus(settings, tmp_path)
    original_chunk_id = legacy.article_chunk_ids("legacy-article")[0]

    report = migrate_legacy_corpus(settings, profile=LocalProfile.ADMIN)
    common = Database(settings.paths.common_database_path)
    rows = common.list_articles(limit=10)

    assert report.source_articles == report.target_articles == 1
    assert report.source_chunks == report.target_chunks == 1
    assert rows[0]["doi"] == doi
    assert common.article_chunk_ids("legacy-article") == [original_chunk_id]
    assert str(rows[0]["pdf_path"]).startswith(str(settings.paths.common_pdf_dir))
    assert not settings.paths.private_database_path.exists()


def test_legacy_migration_is_admin_only(settings, tmp_path) -> None:
    _seed_legacy_corpus(settings, tmp_path)

    with pytest.raises(CorpusMutationForbiddenError):
        migrate_legacy_corpus(settings, profile=LocalProfile.USER)

    assert not settings.paths.common_database_path.exists()


def test_legacy_abstracts_without_full_text_are_indexed_searched_and_citable(
    settings, tmp_path, monkeypatch
) -> None:
    legacy, _doi = _seed_legacy_corpus(settings, tmp_path)
    abstract_id = "11111111-1111-4111-8111-111111111111"
    rejected_id = "22222222-2222-4222-8222-222222222222"
    duplicate_id = "33333333-3333-4333-8333-333333333333"
    _seed_legacy_abstract(
        legacy,
        record_id=abstract_id,
        doi="10.1000/abstract-only",
        title="Microbial succession in cider fermentation",
        abstract="Yeast succession controls cider fermentation aroma.",
    )
    _seed_legacy_abstract(
        legacy,
        record_id=rejected_id,
        doi="10.1000/rejected",
        title="Rejected cider abstract",
        abstract="This rejected source must never enter the common RAG.",
        relevance_status="rejected",
    )
    _seed_legacy_abstract(
        legacy,
        record_id=duplicate_id,
        doi="10.1000/already-full-text",
        title="Already available as full text",
        abstract="This abstract is superseded by a complete article.",
    )

    common = Database(settings.paths.common_database_path)
    common.initialize()
    common.save_article_and_chunks(
        {
            "id": "full-text",
            "sha256": "f" * 64,
            "doi": "10.1000/already-full-text",
            "title": "Already available as full text",
            "abstract": "The complete article abstract.",
            "authors": [],
            "pdf_path": str(tmp_path / "full-text.pdf"),
        },
        [
            {
                "section": "Results",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Complete article evidence.",
                "token_count": 3,
            }
        ],
    )

    report = migrate_legacy_corpus(settings, profile=LocalProfile.ADMIN)
    common_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    store = BibliographicHarvestStore(common)

    assert report.source_abstracts == 2
    assert report.abstracts_imported == 1
    assert report.abstracts_skipped_full_text == 1
    assert store.statistics()["abstracts"] == 1
    assert store.records_by_ids([rejected_id]) == {}
    imported = store.records_by_ids([abstract_id])[abstract_id]
    assert imported["embedding_status"] == "pending"
    assert set(str(imported["sources"]).split(",")) == {"OpenAlex", "legacy"}

    class FakeBackend:
        model_name = common_settings.embeddings.model_name
        dimension = 2

        def encode_documents(self, _texts):
            return [[1.0, 0.0]]

        def encode_queries(self, _texts):
            return [[1.0, 0.0]]

        def close(self):
            pass

    index_report = index_bibliographic_abstracts(
        common_settings,
        store,
        FakeBackend(),
        close_backend=False,
        recreate=True,
    )
    orphan_id = "44444444-4444-4444-8444-444444444444"
    from app.updates.vector_index import BibliographicVectorIndex

    with BibliographicVectorIndex(common_settings) as vector_index:
        vector_index.upsert(
            record_ids=[orphan_id],
            vectors=[[0.0, 1.0]],
            vector_dimension=2,
        )
    prune_report = index_bibliographic_abstracts(
        common_settings,
        store,
        FakeBackend(),
        close_backend=False,
    )
    monkeypatch.setattr(workflows, "SentenceTransformerBackend", lambda _settings: FakeBackend())

    results = workflows.search_common_corpus_abstracts(
        settings,
        query="microbial succession cider",
        limit=5,
    )
    sources = chatbot_sources(results, [results[0].record_id])

    assert index_report.records_indexed == 1
    assert prune_report.records_pruned == 1
    assert results[0].record_id == f"common-abstract:{abstract_id}"
    assert results[0].vector_rank == 1
    assert sources[0].scope is CorpusScope.COMMON
    assert sources[0].evidence_level == "abstract"
    assert set(sources[0].providers) == {"OpenAlex", "legacy"}
