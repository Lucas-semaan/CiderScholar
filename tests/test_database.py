from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from app.database.migrations import CURRENT_SCHEMA_VERSION, ensure_current
from app.database.sqlite import Database


def _article() -> dict[str, object]:
    return {
        "id": "article-1",
        "sha256": "a" * 64,
        "doi": "10.1234/example",
        "title": "Microbiome and fermentation",
        "abstract": "A local test abstract.",
        "authors": ["Ada Test"],
        "journal": "Synthetic Results",
        "work_type": "journal-article",
        "publisher": "Synthetic Press",
        "publication_year": 2025,
        "language": "en",
        "pdf_path": "data/pdf/example.pdf",
        "validation_status": "validated",
        "source": "local",
    }


def test_schema_creates_required_tables_and_fts(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with closing(database.connect()) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    assert {
        "articles",
        "chunks",
        "queries",
        "evidence",
        "ingestion_jobs",
        "article_evidence_runs",
        "synthesis_runs",
        "theme_synthesis_runs",
        "bibliographic_records",
        "bibliographic_record_sources",
        "bibliographic_harvest_runs",
        "bibliographic_harvest_hits",
        "rejected_bibliographic_archive",
        "chat_conversations",
        "chat_messages",
        "argo_request_events",
        "document_elements",
        "document_table_cells",
        "document_element_relations",
        "ocr_page_traces",
        "figure_analysis_runs",
    } <= names
    assert "chunks_fts" in names
    assert "bibliographic_records_fts" in names
    assert "document_element_captions_fts" in names

    with closing(database.connect()) as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert version == CURRENT_SCHEMA_VERSION
        bibliographic_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(bibliographic_records)")
        }
        article_columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
        assert {
            "manual_decision",
            "manual_reviewed_at",
            "work_type",
            "publisher",
        } <= bibliographic_columns
        assert {"work_type", "publisher"} <= article_columns
        unique_indexes = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND name LIKE 'ux_%doi%'
                """
            )
        }
        assert {
            "ux_articles_doi_nocase",
            "ux_bibliographic_records_doi_nocase",
        } <= unique_indexes


def test_schema_repairs_partial_version_30_type_columns() -> None:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            INSERT INTO schema_version(version) VALUES (30);
            CREATE TABLE bibliographic_records (
                id TEXT PRIMARY KEY,
                work_type TEXT,
                publisher TEXT
            );
            CREATE TABLE articles (id TEXT PRIMARY KEY);
            """
        )

        ensure_current(connection)

        article_columns = {row[1] for row in connection.execute("PRAGMA table_info(articles)")}
        bibliographic_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(bibliographic_records)")
        }
        version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]

    assert {"work_type", "publisher"} <= article_columns
    assert {"work_type", "publisher"} <= bibliographic_columns
    assert version == 31


def test_argo_request_events_store_only_quota_metadata(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with closing(database.connect()) as connection, connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(argo_request_events)")}
        connection.execute(
            """
            INSERT INTO argo_request_events(windows_user, endpoint, requested_at)
            VALUES (?, ?, ?)
            """,
            ("test-user", "/models", "2026-07-22T10:00:00+00:00"),
        )
        row = connection.execute(
            "SELECT windows_user, endpoint, requested_at FROM argo_request_events"
        ).fetchone()

    assert columns == {"id", "windows_user", "endpoint", "requested_at"}
    assert tuple(row) == ("test-user", "/models", "2026-07-22T10:00:00+00:00")


def test_argo_request_event_purge_keeps_the_useful_window(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    cutoff = now - timedelta(minutes=180)
    with closing(database.connect()) as connection, connection:
        connection.executemany(
            """
            INSERT INTO argo_request_events(windows_user, endpoint, requested_at)
            VALUES ('test-user', '/models', ?)
            """,
            [
                ((cutoff - timedelta(microseconds=1)).isoformat(),),
                (cutoff.isoformat(),),
                (now.isoformat(),),
            ],
        )

    assert database.purge_argo_request_events(before=cutoff) == 1
    with closing(database.connect()) as connection:
        remaining = connection.execute(
            "SELECT requested_at FROM argo_request_events ORDER BY requested_at"
        ).fetchall()

    assert [row[0] for row in remaining] == [cutoff.isoformat(), now.isoformat()]


def test_chat_conversations_persist_turns_and_cascade_on_delete(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    created = database.create_chat_conversation("  Polyphénols   du cidre  ")
    database.save_chat_turn(
        conversation_id=created["id"],
        user_content="Quel est leur rôle ?",
        assistant_content="Ils contribuent à l’astringence.",
        assistant_response={"answer_markdown": "Ils contribuent à l’astringence."},
        response_time_milliseconds=1250,
    )

    stored = database.chat_conversation(created["id"])
    assert stored is not None
    assert stored["title"] == "Polyphénols du cidre"
    assert stored["message_count"] == 2
    assert [message["role"] for message in stored["messages"]] == ["user", "assistant"]
    assert stored["messages"][1]["response"]["answer_markdown"].startswith("Ils contribuent")
    assert database.list_chat_conversations()[0]["last_message"].startswith("Ils contribuent")

    renamed = database.rename_chat_conversation(created["id"], "Tanins et astringence")
    assert renamed is not None
    assert renamed["title"] == "Tanins et astringence"
    assert database.delete_chat_conversation(created["id"])
    assert database.chat_conversation(created["id"]) is None
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0


def test_fts5_is_populated_by_chunk_trigger(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        _article(),
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": "The microbiome changed during fermentation.",
                "token_count": 7,
            }
        ],
    )
    results = database.lexical_search("microbiome")
    assert len(results) == 1
    assert results[0]["article_id"] == "article-1"


def test_article_and_chunks_are_atomic(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    duplicate_chunks = [
        {
            "page_start": 1,
            "page_end": 1,
            "chunk_index": 0,
            "text": "first",
            "token_count": 1,
        },
        {
            "page_start": 1,
            "page_end": 1,
            "chunk_index": 0,
            "text": "duplicate index",
            "token_count": 2,
        },
    ]
    try:
        database.save_article_and_chunks(_article(), duplicate_chunks)
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("expected duplicate chunk index to fail")
    assert database.article_by_sha256("a" * 64) is None


def test_read_helpers_release_database_file_on_windows(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    assert database.article_by_sha256("f" * 64) is None
    moved = database.path.with_suffix(".moved.sqlite3")
    database.path.replace(moved)
    assert moved.is_file()


def test_article_doi_lookup_is_normalized_and_case_insensitive(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        _article(),
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "DOI identity test.",
                "token_count": 4,
            }
        ],
    )

    stored = database.article_by_doi(" 10.1234/EXAMPLE ")

    assert stored is not None
    assert stored["id"] == "article-1"


def test_corpus_administration_lists_reindexes_and_deletes_history(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        _article(),
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": "A local administrative test fragment.",
                "token_count": 6,
                "embedding_status": "indexed",
            }
        ],
    )
    database.create_query(
        query_id="query-admin",
        original_query="local question",
        expanded_queries=[],
        selected_article_ids=["article-1"],
    )
    database.upsert_ingestion_job(
        pdf_path="data/pdf/failure.pdf",
        sha256="f" * 64,
        state="failed",
        error_type="SyntheticError",
        error_message="technical failure",
    )

    articles = database.list_articles()
    assert len(articles) == 1
    assert articles[0]["chunk_count"] == 1
    assert articles[0]["indexed_chunk_count"] == 1
    assert database.article_chunk_ids("article-1")
    assert database.reset_article_for_reindex("article-1") == 1
    assert database.embedding_status_counts() == {"pending": 1}
    assert database.list_ingestion_jobs(states=["failed"])[0]["error_type"] == ("SyntheticError")
    assert database.list_query_summaries()[0]["id"] == "query-admin"

    assert database.delete_article("article-1") == 1
    assert database.list_articles() == []
    assert database.query_by_id("query-admin") is None
