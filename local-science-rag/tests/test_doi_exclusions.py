from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.database.sqlite import Database
from app.updates.doi_exclusions import DOI_EXCLUSIONS_FILENAME, DoiExclusionRegistry
from app.updates.harvest import BibliographicHarvestStore
from app.updates.models import BibliographicRecord


def test_registry_is_idempotent_and_reinstatement_preserves_history(settings) -> None:
    registry = DoiExclusionRegistry.for_database(settings.paths.database_path)
    occurred_at = datetime(2026, 7, 21, 12, 30, tzinfo=UTC)

    assert registry.exclude(
        "https://doi.org/10.1000/EXCLUDED",
        title="Rejected cider article",
        reason="Manual review",
        origin="manual_review",
        excluded_at=occurred_at,
    )
    assert not registry.exclude(
        "10.1000/excluded",
        title="Rejected cider article",
        reason="Repeated cleanup",
        origin="automatic_relevance_rejection",
    )

    entry = registry.document().entries[0]
    assert entry.doi == "10.1000/excluded"
    assert entry.exclusion_count == 1
    assert entry.first_excluded_at == occurred_at
    assert entry.origins == ["manual_review", "automatic_relevance_rejection"]
    assert registry.is_excluded("10.1000/EXCLUDED")

    second_process_view = DoiExclusionRegistry(registry.path)
    assert second_process_view.is_excluded("10.1000/excluded")
    assert registry.reinstate("10.1000/excluded")
    assert not second_process_view.is_excluded("10.1000/excluded")
    registry.ensure_historical(
        [
            {
                "doi": "10.1000/excluded",
                "title": "Old archive",
                "reason": "Historical rejection",
            }
        ]
    )
    assert not registry.is_excluded("10.1000/excluded")
    assert registry.document().entries[0].reinstated_at is not None

    assert registry.exclude(
        "10.1000/excluded",
        title="Rejected again",
        reason="New explicit rejection",
        origin="manual_review",
    )
    assert registry.document().entries[0].exclusion_count == 2


def test_excluded_doi_is_not_inserted_by_future_harvest(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        settings,
        themes={"microbiologie": "cider yeast"},
        sources=["crossref"],
    )
    store.doi_exclusions.exclude(
        "10.1000/excluded",
        title="Rejected cider article",
        reason="Manual review",
        origin="manual_review",
    )

    record_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/excluded",
            title="Cider yeast ecology",
            abstract="Yeast ecology during cider fermentation.",
            doi="10.1000/excluded",
        ),
    )

    assert record_id is None
    assert store.browse_records()["total"] == 0
    payload = json.loads(
        (settings.paths.database_path.parent / DOI_EXCLUSIONS_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["entries"][0]["active"] is True


def test_registry_reinstates_multiple_dois_atomically(settings) -> None:
    registry = DoiExclusionRegistry.for_database(settings.paths.database_path)
    registry.exclude_many(
        {
            "doi": doi,
            "title": f"Article {index}",
            "reason": "Initial decision",
            "origin": "editorial_review",
        }
        for index, doi in enumerate(("10.1000/one", "10.1000/two"), start=1)
    )

    assert registry.reinstate_many(["10.1000/ONE", "https://doi.org/10.1000/two"]) == 2
    assert registry.reinstate_many(["10.1000/one", "10.1000/two"]) == 0
    assert not registry.is_excluded("10.1000/one")
    assert not registry.is_excluded("10.1000/two")


def test_corrupted_registry_fails_closed(settings) -> None:
    registry = DoiExclusionRegistry.for_database(settings.paths.database_path)
    registry.path.write_text("not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Registre DOI illisible"):
        registry.is_excluded("10.1000/excluded")


def test_existing_sqlite_rejection_archive_is_imported_once(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO rejected_bibliographic_archive (
                original_record_id, canonical_key, doi, title,
                relevance_reason, last_archived_at
            ) VALUES ('old-record', 'doi:10.1000/old', '10.1000/old',
                'Old rejected article', 'Historical rejection', '2025-01-02 03:04:05')
            """
        )

    first = BibliographicHarvestStore(database)
    second = BibliographicHarvestStore(database)

    assert first.doi_exclusions.is_excluded("10.1000/old")
    entries = second.doi_exclusions.document().entries
    assert len(entries) == 1
    assert entries[0].first_excluded_at == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert entries[0].exclusion_count == 1
