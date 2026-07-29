from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app.corpora import (
    LOCAL_PROFILE_ENV,
    CorpusMutationForbiddenError,
    CorpusScope,
    LocalProfile,
    authorize_corpus_mutation,
    corpus_paths,
    load_local_profile,
    settings_for_corpus,
)
from app.database.sqlite import Database
from app.jobs.contracts import PrivateIngestionPayload
from app.jobs.repository import JobRepository
from app.main import create_app
from app.retrieval.lexical_search import LexicalSearchResult
from app.retrieval.vector_search import QdrantLocalIndex


def test_corpus_scopes_are_closed() -> None:
    adapter = TypeAdapter(CorpusScope)

    assert adapter.validate_python("common") is CorpusScope.COMMON
    assert adapter.validate_python("private") is CorpusScope.PRIVATE
    with pytest.raises(ValidationError):
        adapter.validate_python("shared")


def test_retrieval_result_always_carries_an_origin() -> None:
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


def test_common_and_private_paths_are_disjoint_and_created(settings) -> None:
    paths = settings.paths

    assert paths.common_dir.parent == paths.data_dir
    assert paths.private_dir.parent == paths.data_dir
    assert paths.common_dir != paths.private_dir
    assert not paths.common_dir.is_relative_to(paths.private_dir)
    assert not paths.private_dir.is_relative_to(paths.common_dir)
    assert paths.common_database_path.is_file() is False
    assert paths.common_database_path.parent.is_dir()
    assert paths.private_database_path.parent.is_dir()
    assert paths.common_qdrant_dir.is_dir()
    assert paths.private_qdrant_dir.is_dir()


def test_user_profile_cannot_mutate_common_corpus(settings, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(LOCAL_PROFILE_ENV, raising=False)

    with pytest.raises(CorpusMutationForbiddenError):
        authorize_corpus_mutation(CorpusScope.COMMON, load_local_profile())
    with TestClient(create_app(settings)) as client:
        responses = [
            client.post("/api/corpus/folder", json={"folder": str(tmp_path)}),
            client.post("/api/corpus/index", json={"retry_failed": False}),
            client.post("/api/corpus/article-1/reindex"),
            client.delete("/api/corpus/article-1"),
            client.post(
                "/api/corpus/upload",
                files={"files": ("private.pdf", b"%PDF-1.4", "application/pdf")},
            ),
        ]

    assert {response.status_code for response in responses} == {403}


def test_admin_profile_is_local_only_and_can_mutate_common(settings, monkeypatch, tmp_path) -> None:
    profile = load_local_profile({LOCAL_PROFILE_ENV: "admin"})

    authorize_corpus_mutation(CorpusScope.COMMON, profile)
    assert profile is LocalProfile.ADMIN
    assert "profile" not in settings.model_dump(mode="json")["app"]
    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/corpus/folder", json={"folder": str(tmp_path)})

    assert response.status_code == 200
    assert response.json() == {"discovered_files": 0, "reports": []}


def test_sqlite_is_isolated_by_corpus_scope(settings) -> None:
    common = corpus_paths(settings, CorpusScope.COMMON)
    private = corpus_paths(settings, CorpusScope.PRIVATE)

    assert common.database_path != private.database_path
    common_database = Database(common.database_path)
    private_database = Database(private.database_path)
    common_database.initialize()
    private_database.initialize()

    assert common.database_path.is_file()
    assert private.database_path.is_file()
    with common_database.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'articles'"
            ).fetchone()
            is not None
        )
    with private_database.connect() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'articles'"
            ).fetchone()
            is not None
        )


def test_common_and_private_listings_share_the_same_summary_contract(settings) -> None:
    for scope, database_path, state in (
        ("common", settings.paths.common_database_path, "failed"),
        ("private", settings.paths.private_database_path, "ocr_required"),
    ):
        database = Database(database_path)
        database.initialize()
        database.save_article_and_chunks(
            {
                "id": f"{scope}-article",
                "sha256": ("a" if scope == "common" else "b") * 64,
                "title": f"{scope} article",
                "authors": [],
                "pdf_path": str(settings.paths.data_dir / f"{scope}.pdf"),
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
        database.upsert_ingestion_job(
            pdf_path=str(settings.paths.data_dir / f"{scope}.pdf"),
            sha256=("a" if scope == "common" else "b") * 64,
            state=state,
        )

    with TestClient(create_app(settings)) as client:
        common = client.get("/api/corpus")
        private = client.get("/api/private-corpus")

    assert common.status_code == 200
    assert private.status_code == 200
    assert common.json()["scope"] == "common"
    assert private.json()["scope"] == "private"
    assert common.json()["summary"] == {
        "articles": 1,
        "chunks": 1,
        "indexed_chunks": 0,
        "failed_jobs": 1,
        "ocr_jobs": 0,
    }
    assert private.json()["summary"] == {
        "articles": 1,
        "chunks": 1,
        "indexed_chunks": 0,
        "failed_jobs": 0,
        "ocr_jobs": 1,
    }


def test_qdrant_storage_is_isolated_by_corpus_scope(settings) -> None:
    common = corpus_paths(settings, CorpusScope.COMMON)
    private = corpus_paths(settings, CorpusScope.PRIVATE)
    common_index = QdrantLocalIndex(settings, path=common.qdrant_dir)
    private_index = QdrantLocalIndex(settings, path=private.qdrant_dir)

    assert common_index.path == common.qdrant_dir
    assert private_index.path == private.qdrant_dir
    assert common_index.path != private_index.path
    assert common_index.collection_name == private_index.collection_name


def test_private_upload_stages_only_private_paths_and_queues_work(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/private-corpus/upload",
            files={"files": ("research.pdf", b"%PDF-1.4", "application/pdf")},
        )
        listing = client.get("/api/private-corpus")

    private_settings = settings_for_corpus(settings, CorpusScope.PRIVATE)
    payload = response.json()
    job = JobRepository(settings.paths.database_path).get(UUID(payload["job"]["id"]))
    assert response.status_code == 202
    assert listing.json()["scope"] == "private"
    assert listing.json()["summary"]["articles"] == 0
    assert job is not None
    assert isinstance(job.payload, PrivateIngestionPayload)
    stored_path = settings.paths.private_pdf_dir / job.payload.staged_files[0]
    assert stored_path.is_file()
    assert stored_path.is_relative_to(settings.paths.private_pdf_dir)
    assert not stored_path.is_relative_to(settings.paths.common_dir)
    assert private_settings.paths.qdrant_dir == settings.paths.private_qdrant_dir


def test_private_delete_and_reindex_receive_no_common_storage(settings, monkeypatch) -> None:
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

    monkeypatch.setattr("app.api.private_corpus.reindex_article", fake_reindex)
    monkeypatch.setattr("app.api.private_corpus.delete_article", fake_delete)
    with TestClient(create_app(settings)) as client:
        reindexed = client.post("/api/private-corpus/private-1/reindex")
        deleted = client.delete("/api/private-corpus/private-1")

    assert reindexed.status_code == 200
    assert deleted.status_code == 200
    assert len(calls) == 2
    for article_id, active_settings, database in calls:
        assert article_id == "private-1"
        assert active_settings.paths.database_path == settings.paths.private_database_path
        assert active_settings.paths.qdrant_dir == settings.paths.private_qdrant_dir
        assert database.path == settings.paths.private_database_path
        assert database.path != settings.paths.common_database_path
