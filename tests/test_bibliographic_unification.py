from __future__ import annotations

from pathlib import Path

from app.database.sqlite import Database
from app.services.bibliographic_unification import merge_bibliographic_runs


def _insert_record(
    database: Database,
    *,
    record_id: str,
    doi: str,
    title: str,
    status: str,
    abstract: str | None,
    manual_decision: str | None = None,
) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors,
                content_hash, embedding_status, relevance_status,
                relevance_score, relevance_reason, manual_decision
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, 'not_applicable', ?, 0.9, ?, ?)
            """,
            (
                record_id,
                f"doi:{doi}",
                doi,
                title,
                abstract,
                "a" * 64,
                status,
                "audited",
                manual_decision,
            ),
        )


def test_selected_legacy_runs_merge_into_one_scientific_store(settings) -> None:
    source = Database(settings.paths.database_path)
    target = Database(settings.paths.scientific_database_path)
    source.initialize()
    target.initialize()
    _insert_record(
        source,
        record_id="source-existing",
        doi="10.1000/existing",
        title="Source title",
        status="accepted",
        abstract="Source abstract",
    )
    _insert_record(
        source,
        record_id="source-new",
        doi="10.1000/new",
        title="New record",
        status="review",
        abstract=None,
    )
    _insert_record(
        target,
        record_id="target-existing",
        doi="10.1000/existing",
        title="Curated target title",
        status="accepted",
        abstract=None,
        manual_decision="accepted",
    )
    with source.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_runs (
                id, profile, state, themes, sources, per_source_limit,
                request_delay_seconds, raw_record_count, unique_record_count,
                abstract_record_count, accepted_record_count,
                accepted_abstract_count, errors, completed_at
            ) VALUES (
                'run-1', 'test', 'completed', '{}', '["Crossref"]', 10,
                0.0, 2, 2, 1, 1, 1, '[]', CURRENT_TIMESTAMP
            )
            """
        )
        for rank, record_id, status in (
            (1, "source-existing", "accepted"),
            (2, "source-new", "review"),
        ):
            connection.execute(
                """
                INSERT INTO bibliographic_harvest_hits (
                    run_id, theme, record_id, source, rank,
                    relevance_status, relevance_score, relevance_reason
                ) VALUES ('run-1', 'cidre', ?, 'Crossref', ?, ?, 0.9, 'audited')
                """,
                (record_id, rank, status),
            )
            connection.execute(
                """
                INSERT INTO bibliographic_record_sources (record_id, source, source_id)
                VALUES (?, 'Crossref', ?)
                """,
                (record_id, f"provider-{rank}"),
            )
            connection.execute(
                """
                INSERT INTO full_text_assets (
                    id, record_id, doi, source, source_url, state
                ) VALUES (?, ?, ?, 'istex', 'https://example.test/pdf', 'available')
                """,
                (f"pdf-{rank}", record_id, f"10.1000/{'existing' if rank == 1 else 'new'}"),
            )
        connection.execute(
            """
            INSERT INTO native_full_text_assets (
                id, record_id, doi, source, format, source_url, media_type, state
            ) VALUES (
                'native-1', 'source-new', '10.1000/new', 'istex', 'tei_xml',
                'https://example.test/tei', 'application/xml', 'available'
            )
            """
        )

    report = merge_bibliographic_runs(source, target, run_ids=["run-1"])

    assert report.source_records == 2
    assert report.inserted_records == 1
    assert report.matched_records == 1
    assert report.inserted_runs == 1
    assert report.inserted_hits == 2
    assert report.inserted_sources == 2
    assert report.inserted_pdf_assets == 2
    assert report.inserted_native_assets == 1
    assert report.target_records == report.target_fts_records == 2
    with target.connect() as connection:
        curated = connection.execute(
            "SELECT title, relevance_status, manual_decision FROM bibliographic_records "
            "WHERE id = 'target-existing'"
        ).fetchone()
        assert tuple(curated) == ("Curated target title", "accepted", "accepted")
        mapped = connection.execute(
            "SELECT DISTINCT record_id FROM full_text_assets ORDER BY record_id"
        ).fetchall()
        assert [row[0] for row in mapped] == ["source-new", "target-existing"]

    repeated = merge_bibliographic_runs(source, target, run_ids=["run-1"])

    assert repeated.inserted_records == 0
    assert repeated.matched_records == 2
    assert repeated.inserted_runs == 0
    assert repeated.inserted_hits == 0
    assert repeated.inserted_pdf_assets == 0
    assert repeated.inserted_native_assets == 0
    assert repeated.target_records == repeated.target_fts_records == 2


def test_persistent_bibliographic_entrypoints_scope_to_the_scientific_store() -> None:
    root = Path(__file__).resolve().parents[1]
    entrypoints = (
        "scripts/harvest_cider_pilot.py",
        "scripts/harvest_cider_bulk.py",
        "scripts/harvest_full_text.py",
        "scripts/grow_full_text_rag.py",
        "scripts/import_ifpc_publications.py",
        "scripts/manage_doi_exclusions.py",
        "scripts/review_historical_titles.py",
        "scripts/reconsider_editorial_rejections.py",
        "scripts/rank_articles.py",
        "scripts/extract_article_evidence.py",
        "scripts/synthesize_query.py",
        "scripts/benchmark_system.py",
        "scripts/create_demo_corpus.py",
    )

    for relative_path in entrypoints:
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "settings_for_corpus(load_settings" in source, relative_path
