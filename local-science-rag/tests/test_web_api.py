from __future__ import annotations

from contextlib import closing

from fastapi.testclient import TestClient

from app.database.sqlite import Database
from app.main import create_app
from app.updates.doi_exclusions import DoiExclusionRegistry
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import BibliographicVectorIndex

REVIEW_ACCEPTED_ID = "11111111-1111-4111-8111-111111111111"
REVIEW_REJECTED_ID = "22222222-2222-4222-8222-222222222222"


def _insert_review_record(settings, record_id: str) -> Database:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_runs (
                id, profile, state, themes, sources, per_source_limit,
                request_delay_seconds
            ) VALUES ('review-run', 'test', 'completed', '[]', '[]', 1, 0)
            ON CONFLICT(id) DO NOTHING
            """
        )
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, content_hash,
                embedding_status, relevance_status, relevance_score,
                relevance_reason, relevance_theme
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, 'not_applicable', 'review', 0.55, ?, ?)
            """,
            (
                record_id,
                f"doi:10.1000/{record_id}",
                f"10.1000/{record_id}",
                f"Notice à réviser {record_id}",
                "Cider fermentation is mentioned without a decisive thematic match.",
                "a" * 64,
                "Qualification automatique incertaine",
                "microbiologie",
            ),
        )
        connection.execute(
            """
            INSERT INTO bibliographic_record_sources (record_id, source, source_id)
            VALUES (?, 'Crossref', ?)
            """,
            (record_id, record_id),
        )
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_hits (
                run_id, theme, record_id, source, rank,
                relevance_status, relevance_score, relevance_reason
            ) VALUES ('review-run', 'microbiologie', ?, 'Crossref', 1,
                'review', 0.55, 'Qualification automatique incertaine')
            """,
            (record_id,),
        )
    return database


def test_web_overview_and_library_are_available_on_an_empty_database(settings) -> None:
    with TestClient(create_app(settings)) as client:
        overview = client.get("/api/system/overview")
        corpus = client.get("/api/corpus")
        library = client.get("/api/library/records")

    assert overview.status_code == 200
    assert overview.json()["corpus"] == {
        "common": {
            "articles": 0,
            "chunks": 0,
            "indexed_chunks": 0,
            "index_coverage": 0.0,
            "failed_ingestions": 0,
            "ocr_required": 0,
        },
        "private": {
            "articles": 0,
            "chunks": 0,
            "indexed_chunks": 0,
            "index_coverage": 0.0,
            "failed_ingestions": 0,
            "ocr_required": 0,
        },
    }
    assert corpus.status_code == 200
    assert corpus.json()["summary"]["articles"] == 0
    assert library.status_code == 200
    assert library.json() == {"records": [], "total": 0, "limit": 50, "offset": 0}


def test_overview_keeps_common_and_private_statistics_separate(settings) -> None:
    for scope, database_path, count in (
        ("common", settings.paths.common_database_path, 1),
        ("private", settings.paths.private_database_path, 2),
    ):
        database = Database(database_path)
        database.initialize()
        for index in range(count):
            database.save_article_and_chunks(
                {
                    "id": f"{scope}-{index}",
                    "sha256": (str(index + 1) if scope == "common" else str(index + 5)) * 64,
                    "title": f"{scope} article {index}",
                    "authors": [],
                    "pdf_path": str(settings.paths.data_dir / f"{scope}-{index}.pdf"),
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
        corpus = client.get("/api/system/overview").json()["corpus"]

    assert corpus["common"]["articles"] == 1
    assert corpus["private"]["articles"] == 2
    assert corpus["common"]["chunks"] == 1
    assert corpus["private"]["chunks"] == 2


def test_review_notice_can_be_admitted_and_manual_decision_is_preserved(settings) -> None:
    database = _insert_review_record(settings, REVIEW_ACCEPTED_ID)
    registry = DoiExclusionRegistry.for_database(database.path)
    registry.exclude(
        f"10.1000/{REVIEW_ACCEPTED_ID}",
        title="Previously rejected notice",
        reason="Previous rejection",
        origin="manual_review",
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/library/records/{REVIEW_ACCEPTED_ID}/decision",
            json={"decision": "accepted"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "record_id": REVIEW_ACCEPTED_ID,
        "title": f"Notice à réviser {REVIEW_ACCEPTED_ID}",
        "decision": "accepted",
        "deleted": False,
        "vectors_deleted": 0,
    }
    with database.transaction() as connection:
        BibliographicHarvestStore._refresh_record_relevance(connection, REVIEW_ACCEPTED_ID)
    with closing(database.connect()) as connection:
        stored = connection.execute(
            """
            SELECT relevance_status, embedding_status, manual_decision, manual_reviewed_at
            FROM bibliographic_records WHERE id = ?
            """,
            (REVIEW_ACCEPTED_ID,),
        ).fetchone()
    assert stored is not None
    assert stored["relevance_status"] == "accepted"
    assert stored["embedding_status"] == "pending"
    assert stored["manual_decision"] == "accepted"
    assert stored["manual_reviewed_at"] is not None
    assert not registry.is_excluded(f"10.1000/{REVIEW_ACCEPTED_ID}")


def test_review_rejection_removes_all_record_data(settings) -> None:
    database = _insert_review_record(settings, REVIEW_REJECTED_ID)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO rejected_bibliographic_archive (
                original_record_id, canonical_key, title
            ) VALUES (?, ?, 'Ancienne archive')
            """,
            (REVIEW_REJECTED_ID, f"doi:10.1000/{REVIEW_REJECTED_ID}"),
        )
    with BibliographicVectorIndex(settings) as index:
        index.upsert(
            record_ids=[REVIEW_REJECTED_ID],
            vectors=[[0.1, 0.2]],
            vector_dimension=2,
        )
        assert index.count() == 1

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/library/records/{REVIEW_REJECTED_ID}/decision",
            json={"decision": "rejected"},
        )

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["vectors_deleted"] == 1
    assert DoiExclusionRegistry.for_database(database.path).is_excluded(
        f"10.1000/{REVIEW_REJECTED_ID}"
    )
    with BibliographicVectorIndex(settings) as index:
        assert index.count() == 0
    with closing(database.connect()) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM bibliographic_records WHERE id = ?",
                (REVIEW_REJECTED_ID,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM bibliographic_record_sources
                WHERE record_id = ?""",
                (REVIEW_REJECTED_ID,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM bibliographic_harvest_hits
                WHERE record_id = ?""",
                (REVIEW_REJECTED_ID,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM bibliographic_records_fts WHERE record_id = ?",
                (REVIEW_REJECTED_ID,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM rejected_bibliographic_archive
                WHERE original_record_id = ?""",
                (REVIEW_REJECTED_ID,),
            ).fetchone()[0]
            == 0
        )


def test_runtime_settings_are_session_scoped_and_never_expose_secrets(
    settings, monkeypatch
) -> None:
    settings.app.offline_mode = False
    monkeypatch.setenv(settings.argo.api_key_env, "secret-that-must-not-leak")
    with TestClient(create_app(settings)) as client:
        updated = client.put(
            "/api/system/settings",
            json={
                "default_article_count": 12,
                "lexical_weight": 0.4,
                "vector_weight": 0.5,
                "reranker_weight": 0.1,
                "embedding_batch_size": 4,
                "passages_per_article": 6,
            },
        )
        persisted = client.get("/api/system/settings")

    assert updated.status_code == 200
    assert persisted.status_code == 200
    payload = persisted.json()
    assert payload["llm_provider"] == "argo"
    assert payload["llm_key_configured"] is True
    assert payload["retrieval"]["default_article_count"] == 12
    assert "secret-that-must-not-leak" not in persisted.text


def test_api_contract_rejects_unknown_runtime_fields(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/api/system/settings",
            json={
                "default_article_count": 20,
                "lexical_weight": 0.35,
                "vector_weight": 0.45,
                "reranker_weight": 0.2,
                "embedding_batch_size": 8,
                "passages_per_article": 5,
                "api_key": "forbidden",
            },
        )

    assert response.status_code == 422


def test_synthesis_without_source_valid_evidence_is_a_domain_conflict(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.create_query(
        query_id="query-without-evidence",
        original_query="Question sans preuve",
        expanded_queries=[],
        selected_article_ids=[],
    )

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/synthesis/query-without-evidence/run", json={"resume": True})

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "Impossible de synthétiser cette analyse : aucune preuve factuelle "
            "validée n’est disponible ou la traçabilité des sources est incomplète."
        )
    }


def test_synchronous_chatbot_route_is_removed(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/chatbot",
            json={"message": "Question qui ne doit pas être traitée synchroniquement"},
        )

    assert response.status_code == 405


def test_chatbot_conversation_history_can_be_managed_and_reloaded(settings) -> None:
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/chatbot/conversations", json={"title": "Levures et arômes"})
        conversation_id = created.json()["id"]
        turn = client.post(
            f"/api/chatbot/conversations/{conversation_id}/jobs",
            json={
                "message": "Quel rôle jouent les levures ?",
                "client_request_id": "11111111-1111-4111-8111-111111111111",
            },
        )
        listed = client.get("/api/chatbot/conversations")
        detail = client.get(f"/api/chatbot/conversations/{conversation_id}")
        renamed = client.put(
            f"/api/chatbot/conversations/{conversation_id}",
            json={"title": "Levures non-Saccharomyces"},
        )
        deleted = client.delete(f"/api/chatbot/conversations/{conversation_id}")
        missing = client.get(f"/api/chatbot/conversations/{conversation_id}")

    assert created.status_code == 201
    assert turn.status_code == 202
    assert listed.json()["conversations"][0]["message_count"] == 1
    assert detail.json()["messages"][0]["content"] == "Quel rôle jouent les levures ?"
    assert renamed.json()["title"] == "Levures non-Saccharomyces"
    assert deleted.json() == {"deleted": True}
    assert missing.status_code == 404
