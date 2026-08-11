from __future__ import annotations

import pytest

from app.corpora import LocalProfile
from app.database.sqlite import Database
from app.services.evidence_migration import EvidenceMigrationError, migrate_legacy_evidence


def _seed_matching_article(database: Database, tmp_path) -> None:
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "a" * 64,
            "title": "Preuve migrée",
            "authors": ["Auteur Test"],
            "pdf_path": str(tmp_path / "article.pdf"),
        },
        [
            {
                "section": "Results",
                "page_start": 2,
                "page_end": 2,
                "chunk_index": 0,
                "text": "Le résultat source est traçable.",
                "token_count": 5,
            }
        ],
    )


def _seed_legacy_evidence(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO queries (id, original_query, expanded_queries, selected_article_ids)
            VALUES ('query-1', 'Question scientifique', '[]', '["article-1"]')
            """
        )
        connection.execute(
            """
            INSERT INTO article_evidence_runs (
                query_id, article_id, state, selected_chunk_ids
            ) VALUES ('query-1', 'article-1', 'completed', '[1]')
            """
        )
        connection.execute(
            """
            INSERT INTO evidence (
                id, query_id, article_id, chunk_id, claim, source_excerpt,
                page_start, page_end, relevance_score
            ) VALUES (
                'evidence-1', 'query-1', 'article-1', 1, 'Résultat observé',
                'Le résultat source est traçable.', 2, 2, 0.9
            )
            """
        )
        connection.execute(
            """
            INSERT INTO synthesis_runs (query_id, state, cited_evidence_ids)
            VALUES ('query-1', 'processing', '["evidence-1"]')
            """
        )
        connection.execute(
            """
            INSERT INTO theme_synthesis_runs (
                query_id, theme_id, state, theme_label, article_ids
            ) VALUES ('query-1', 'theme-1', 'pending', 'Thème', '["article-1"]')
            """
        )


def _count(database: Database, table: str) -> int:
    with database.connect() as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_evidence_migration_is_dry_run_then_verified_idempotent_apply(settings, tmp_path) -> None:
    legacy = Database(settings.paths.database_path)
    common = Database(settings.paths.common_database_path)
    _seed_matching_article(legacy, tmp_path)
    _seed_matching_article(common, tmp_path)
    _seed_legacy_evidence(legacy)

    preview = migrate_legacy_evidence(settings, profile=LocalProfile.ADMIN)

    assert preview.applied is False
    assert preview.backup_path is None
    assert preview.source_counts == {
        "queries": 1,
        "article_evidence_runs": 1,
        "evidence": 1,
        "synthesis_runs": 1,
        "theme_synthesis_runs": 1,
    }
    assert preview.inserted_counts == dict.fromkeys(preview.source_counts, 0)
    assert _count(common, "evidence") == 0

    applied = migrate_legacy_evidence(settings, profile=LocalProfile.ADMIN, apply=True)

    assert applied.backup_path is not None
    assert applied.inserted_counts == dict.fromkeys(preview.source_counts, 1)
    assert all(_count(common, table) == 1 for table in preview.source_counts)
    assert _count(legacy, "evidence") == 1

    rerun = migrate_legacy_evidence(settings, profile=LocalProfile.ADMIN, apply=True)

    assert rerun.inserted_counts == dict.fromkeys(preview.source_counts, 0)
    assert rerun.already_present_counts == dict.fromkeys(preview.source_counts, 1)


def test_evidence_migration_aborts_on_existing_conflicting_query(settings, tmp_path) -> None:
    legacy = Database(settings.paths.database_path)
    common = Database(settings.paths.common_database_path)
    _seed_matching_article(legacy, tmp_path)
    _seed_matching_article(common, tmp_path)
    _seed_legacy_evidence(legacy)
    with common.transaction() as connection:
        connection.execute(
            """
            INSERT INTO queries (id, original_query, expanded_queries, selected_article_ids)
            VALUES ('query-1', 'Question différente', '[]', '["article-1"]')
            """
        )

    with pytest.raises(EvidenceMigrationError, match="conflicting queries"):
        migrate_legacy_evidence(settings, profile=LocalProfile.ADMIN)

    assert _count(common, "evidence") == 0
