from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app.corpora import CorpusScope, corpus_paths, settings_for_corpus
from app.database.sqlite import Database
from app.main import create_app
from app.retrieval.lexical_search import LexicalSearchResult
from app.retrieval.vector_search import QdrantLocalIndex


def test_corpus_scope_has_one_authoritative_value() -> None:
    adapter = TypeAdapter(CorpusScope)

    assert adapter.validate_python("common") is CorpusScope.COMMON
    with pytest.raises(ValidationError):
        adapter.validate_python("private")
    with pytest.raises(ValidationError):
        adapter.validate_python("shared")


def test_retrieval_result_always_carries_the_common_origin() -> None:
    result = LexicalSearchResult(
        rank=1,
        chunk_id=1,
        article_id="article-1",
        article_title="Titre",
        publication_year=2026,
        section=None,
        page_start=1,
        page_end=1,
        text="Preuve",
        bm25_score=-1.0,
        relevance_score=1.0,
    )

    assert result.scope is CorpusScope.COMMON
    assert result.model_dump(mode="json")["scope"] == "common"


def test_only_common_corpus_paths_are_created(settings) -> None:
    paths = corpus_paths(settings, CorpusScope.COMMON)

    assert paths.root == settings.paths.common_dir
    assert paths.database_path == settings.paths.common_database_path
    assert paths.database_path.parent.is_dir()
    assert paths.pdf_dir.is_dir()
    assert paths.extracted_dir.is_dir()
    assert paths.qdrant_dir.is_dir()
    assert not (settings.paths.data_dir / "private").exists()


def test_corpus_settings_redirect_every_scientific_path(settings) -> None:
    scoped = settings_for_corpus(settings, CorpusScope.COMMON)
    paths = corpus_paths(settings, CorpusScope.COMMON)

    assert scoped.paths.database_path == paths.database_path
    assert scoped.paths.pdf_dir == paths.pdf_dir
    assert scoped.paths.extracted_dir == paths.extracted_dir
    assert scoped.paths.qdrant_dir == paths.qdrant_dir


def test_every_local_profile_can_ingest_the_common_corpus(settings, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("CIDERSCHOLAR_LOCAL_PROFILE", raising=False)

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/corpus/folder", json={"folder": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {"discovered_files": 0, "reports": []}


def test_corpus_listing_is_single_and_has_no_scope_field(settings) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "a" * 64,
            "title": "Common article",
            "authors": [],
            "pdf_path": str(settings.paths.common_pdf_dir / "article.pdf"),
        },
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Scientific evidence",
                "token_count": 2,
            }
        ],
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/corpus")
        removed_route = client.get("/api/private-corpus")

    assert response.status_code == 200
    assert "scope" not in response.json()
    assert response.json()["summary"] == {
        "articles": 1,
        "chunks": 1,
        "indexed_chunks": 0,
        "failed_jobs": 0,
        "ocr_jobs": 0,
    }
    assert removed_route.status_code == 404


def test_article_listing_is_not_truncated_at_five_thousand(settings) -> None:
    database = Database(settings.paths.common_database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.executemany(
            """
            INSERT INTO articles (id, sha256, title, authors, pdf_path)
            VALUES (?, ?, ?, '[]', ?)
            """,
            (
                (
                    f"article-{index}",
                    f"{index:064x}",
                    f"Article {index}",
                    str(settings.paths.common_pdf_dir / f"{index}.pdf"),
                )
                for index in range(5_101)
            ),
        )

    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/corpus").json()

    assert payload["summary"]["articles"] == 5_101
    assert len(payload["articles"]) == 5_101


def test_qdrant_uses_the_common_corpus_path(settings) -> None:
    common = corpus_paths(settings, CorpusScope.COMMON)
    index = QdrantLocalIndex(settings, path=common.qdrant_dir)

    try:
        assert index.path == common.qdrant_dir
    finally:
        index.close()


def test_delete_and_reindex_receive_common_storage(settings, monkeypatch) -> None:
    calls: list[tuple[str, object, object]] = []

    class FakeReport:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            return {"mode": mode}

    def fake_reindex(active_settings, database, *, article_id):
        calls.append((article_id, active_settings, database))
        return FakeReport()

    def fake_delete(active_settings, database, *, article_id):
        calls.append((article_id, active_settings, database))
        return {"deleted_chunks": 0}

    monkeypatch.setattr("app.api.ingestion.reindex_article", fake_reindex)
    monkeypatch.setattr("app.api.ingestion.delete_article", fake_delete)
    with TestClient(create_app(settings)) as client:
        reindexed = client.post("/api/corpus/article-1/reindex")
        deleted = client.delete("/api/corpus/article-1")

    assert reindexed.status_code == 200
    assert deleted.status_code == 200
    assert len(calls) == 2
    for article_id, active_settings, database in calls:
        assert article_id == "article-1"
        assert active_settings.paths.database_path == settings.paths.common_database_path
        assert active_settings.paths.qdrant_dir == settings.paths.common_qdrant_dir
        assert database.path == settings.paths.common_database_path
