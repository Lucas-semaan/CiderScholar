from __future__ import annotations

import sqlite3

import pytest

from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord
from app.updates.vector_index import (
    BibliographicVectorIndex,
    index_bibliographic_abstracts,
    verify_bibliographic_abstract_index,
)
from scripts import rebuild_bibliographic_abstract_index


class _Backend:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.dimension = 2

    def encode_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def close(self) -> None:
        pass


def _store_with_eligible_and_excluded_records(settings) -> tuple[BibliographicHarvestStore, str]:
    common = settings_for_corpus(settings, CorpusScope.COMMON)
    database = Database(common.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        common,
        themes={"microbiologie": "cider yeast"},
        sources=["openalex"],
    )
    records = [
        (
            "Yeast ecology in cider",
            "Cider yeast fermentation evidence.",
            "10.1000/abstract-only",
        ),
        (
            "Yeast ecology in cider full text",
            "Cider yeast full text duplicate.",
            "10.1000/full-text",
        ),
        (
            "Yeast ecology in cider invalid DOI",
            "Cider yeast invalid DOI.",
            "10.1000/invalid",
        ),
    ]
    for rank, (title, abstract, doi) in enumerate(records, start=1):
        store.upsert_hit(
            run_id=run_id,
            theme="microbiologie",
            rank=rank,
            record=BibliographicRecord(
                source="openalex",
                source_id=f"record-{rank}",
                title=title,
                abstract=abstract,
                doi=doi,
            ),
        )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE bibliographic_records SET doi = ? WHERE doi = ?",
            ("not-a-doi", "10.1000/invalid"),
        )
    database.save_article_and_chunks(
        {
            "id": "full-text-article",
            "sha256": "a" * 64,
            "doi": "10.1000/full-text",
            "title": "Full text duplicate",
            "pdf_path": "data/common/pdf/full-text.pdf",
        },
        [],
    )
    return store, "10.1000/abstract-only"


def test_abstract_index_excludes_full_text_and_invalid_dois_and_verifies_payloads(settings) -> None:
    common = settings_for_corpus(settings, CorpusScope.COMMON)
    store, _ = _store_with_eligible_and_excluded_records(settings)
    backend = _Backend(common.embeddings.model_name)

    report = index_bibliographic_abstracts(
        common,
        store,
        backend,
        recreate=True,
        close_backend=False,
    )

    assert report.eligible_records == 1
    assert report.records_indexed == 1
    index = BibliographicVectorIndex(common)
    try:
        assert index.record_ids()
    finally:
        index.close()

    verification = verify_bibliographic_abstract_index(common, store)
    assert verification.eligible_record_count == 1
    assert verification.qdrant_point_count == 1

    index = BibliographicVectorIndex(common)
    try:
        index.index.client.set_payload(
            collection_name=index.collection_name,
            payload={"record_id": "wrong"},
            points=index.record_ids(),
            wait=True,
        )
    finally:
        index.close()

    with pytest.raises(RuntimeError, match="payload"):
        verify_bibliographic_abstract_index(common, store)

    database = Database(common.paths.database_path)
    with sqlite3.connect(database.path) as connection:
        statuses = dict(
            connection.execute("SELECT doi, embedding_status FROM bibliographic_records")
        )
    assert statuses["10.1000/abstract-only"] == "indexed"
    assert statuses["10.1000/full-text"] == "not_applicable"
    assert statuses["not-a-doi"] == "not_applicable"


def test_verify_cli_routes_to_common_and_does_not_load_embedding_backend(
    settings, monkeypatch, capsys
) -> None:
    common = settings_for_corpus(settings, CorpusScope.COMMON)
    Database(common.paths.database_path).initialize()
    observed = {}

    monkeypatch.setattr(
        rebuild_bibliographic_abstract_index, "load_settings", lambda _path: settings
    )

    class Result:
        def model_dump(self, **_kwargs):
            return {"verified": True}

    def verify(scoped, _store):
        observed["path"] = scoped.paths.database_path
        return Result()

    monkeypatch.setattr(
        rebuild_bibliographic_abstract_index, "verify_bibliographic_abstract_index", verify
    )
    monkeypatch.setattr(
        rebuild_bibliographic_abstract_index,
        "SentenceTransformerBackend",
        lambda _settings: (_ for _ in ()).throw(AssertionError("backend must not load")),
    )

    assert rebuild_bibliographic_abstract_index.main(["--verify"]) == 0
    assert observed["path"] == common.paths.database_path
    assert '"verified": true' in capsys.readouterr().out


def test_abstract_index_retries_only_failed_eligible_records(settings) -> None:
    common = settings_for_corpus(settings, CorpusScope.COMMON)
    store, _ = _store_with_eligible_and_excluded_records(settings)

    class FailingBackend(_Backend):
        def encode_documents(self, _texts):
            raise RuntimeError("intentional embedding failure")

    failed = index_bibliographic_abstracts(
        common,
        store,
        FailingBackend(common.embeddings.model_name),
        recreate=True,
        raise_on_error=False,
        retry_failed=False,
    )
    assert failed.records_failed == 1
    assert failed.error_type == "RuntimeError"

    resumed = index_bibliographic_abstracts(
        common,
        store,
        _Backend(common.embeddings.model_name),
        retry_failed=True,
    )
    assert resumed.records_indexed == 1
    assert verify_bibliographic_abstract_index(common, store).verified is True


def test_recreate_removes_points_that_became_ineligible_on_windows(settings) -> None:
    common = settings_for_corpus(settings, CorpusScope.COMMON)
    database = Database(common.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        common,
        themes={"microbiologie": "cider yeast"},
        sources=["openalex"],
    )
    for rank, doi in enumerate(("10.1000/keep", "10.1000/full-text-later"), start=1):
        store.upsert_hit(
            run_id=run_id,
            theme="microbiologie",
            rank=rank,
            record=BibliographicRecord(
                source="openalex",
                source_id=f"recreate-{rank}",
                title=f"Eligible abstract {rank}",
                abstract="Traceable abstract evidence.",
                doi=doi,
            ),
        )
    with database.transaction() as connection:
        connection.execute("UPDATE bibliographic_records SET relevance_status = 'accepted'")

    first = index_bibliographic_abstracts(
        common,
        store,
        _Backend(common.embeddings.model_name),
        recreate=True,
    )
    assert first.records_indexed == 2

    database.save_article_and_chunks(
        {
            "id": "later-full-text",
            "sha256": "b" * 64,
            "doi": "10.1000/full-text-later",
            "title": "Later full text",
            "pdf_path": "data/common/pdf/later.pdf",
        },
        [],
    )
    recreated = index_bibliographic_abstracts(
        common,
        store,
        _Backend(common.embeddings.model_name),
        recreate=True,
    )

    assert recreated.eligible_records == recreated.records_indexed == 1
    assert verify_bibliographic_abstract_index(common, store).qdrant_point_count == 1
