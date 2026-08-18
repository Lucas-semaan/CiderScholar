from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.updates.models import BibliographicRecord
from scripts.harvest_semantic_scholar_discovery import (
    _existing_dois,
    _jobs,
    _parse_retry_at,
    _record_deferred_checkpoint,
    _result_key,
    build_parser,
)


def test_semantic_scholar_discovery_parser_defaults_to_all_query_sets(tmp_path) -> None:
    arguments = build_parser().parse_args(["--run-dir", str(tmp_path)])

    assert arguments.provider == "semantic_scholar"
    assert set(arguments.query_sets) == {
        "focused",
        "expanded",
        "specialized",
        "materials",
        "microbiology",
    }
    assert arguments.pages_per_query == 10
    assert arguments.max_results == 40_000


def test_official_discovery_parser_accepts_zenodo(tmp_path) -> None:
    arguments = build_parser().parse_args(
        ["--provider", "zenodo", "--page-size", "25", "--run-dir", str(tmp_path)]
    )

    assert arguments.provider == "zenodo"
    assert arguments.page_size == 25


def test_semantic_scholar_jobs_cover_each_theme_per_page() -> None:
    jobs = _jobs(query_sets=("materials",), pages=2)

    assert {job["theme"] for job in jobs} == {
        "biochimie",
        "microbiologie",
        "polyphenols",
        "proteines",
        "jus_pomme",
        "calvados_eau_vie",
        "pommeau",
        "aromes_procede",
    }
    assert {job["page"] for job in jobs} == {0, 1}
    first_page_count = sum(job["page"] == 0 for job in jobs)
    assert first_page_count == sum(job["page"] == 1 for job in jobs)


def test_semantic_scholar_result_key_prefers_normalized_doi() -> None:
    record = BibliographicRecord(
        source="Semantic Scholar",
        source_id="paper-1",
        title="Cider fermentation",
        doi="10.1000/cider",
        url="https://www.semanticscholar.org/paper/paper-1",
    )

    assert _result_key(record) == "doi:10.1000/cider"


def test_semantic_scholar_existing_dois_reads_database_without_mutation(tmp_path) -> None:
    database_path = tmp_path / "corpus.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE bibliographic_records (doi TEXT)")
        connection.execute("INSERT INTO bibliographic_records VALUES (?)", ("10.1000/CIDER",))
        connection.execute("INSERT INTO bibliographic_records VALUES (NULL)")

    assert _existing_dois(database_path) == {"10.1000/cider"}


def test_semantic_scholar_retry_window_requires_an_aware_timestamp() -> None:
    assert _parse_retry_at("2026-08-13T13:29:52+00:00") == datetime(
        2026, 8, 13, 13, 29, 52, tzinfo=UTC
    )
    assert _parse_retry_at("2026-08-13T13:29:52") is None
    assert _parse_retry_at("not-a-date") is None


def test_semantic_scholar_defer_window_is_checkpointed_before_waiting() -> None:
    checkpoint = {"state": "running"}
    retry_at = datetime(2026, 8, 13, 13, 30, 58, tzinfo=UTC)

    _record_deferred_checkpoint(
        checkpoint,
        retry_at=retry_at,
        completed_jobs=1,
        unique_results=10,
    )

    assert checkpoint["state"] == "deferred"
    assert checkpoint["next_retry_at"] == retry_at.isoformat()
    assert checkpoint["completed_jobs"] == 1
    assert checkpoint["unique_results"] == 10
