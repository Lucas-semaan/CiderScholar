from __future__ import annotations

import json

import pytest

from app.database.sqlite import Database
from app.updates.models import BibliographicRecord
from scripts.import_web_discovery import (
    _apply_validated,
    _clean_web_title,
    _load_candidates,
    _require_no_running_harvest,
    _validate_candidate,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Apple pomace fermentation | Scientific Reports", "Apple pomace fermentation"),
        ("Apple juice phenolics - ScienceDirect", "Apple juice phenolics"),
        ("A title - legitimate subtitle", "A title - legitimate subtitle"),
    ],
)
def test_only_known_engine_publisher_suffixes_are_removed(raw, expected) -> None:
    assert _clean_web_title(raw) == expected


def test_web_candidates_are_deduplicated_by_engine_and_url(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    row = {
        "engine": "brave",
        "theme": "microbiologie",
        "url": "https://example.test/paper/",
        "title": "Cider yeast ecology",
        "doi": "10.1000/TEST",
    }
    (first / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (second / "results.jsonl").write_text(
        json.dumps({**row, "url": "https://example.test/paper"}) + "\n",
        encoding="utf-8",
    )

    candidates = _load_candidates([first, second])

    assert len(candidates) == 1
    assert candidates[0]["doi"] == "10.1000/test"


def test_citation_candidate_provenance_is_preserved(tmp_path) -> None:
    run_dir = tmp_path / "citations"
    run_dir.mkdir()
    (run_dir / "results.jsonl").write_text(
        json.dumps(
            {
                "engine": "opencitations",
                "theme": "microbiologie",
                "url": "https://doi.org/10.1000/cider",
                "title": "Cider yeast ecology",
                "doi": "10.1000/cider",
                "seed_doi": "10.1000/seed",
                "relation": "citation",
                "citation_provider": "OpenCitations Index v2",
                "relation_count": 3,
                "discovered_at": "2026-08-13T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    candidate = _load_candidates([run_dir])[0]

    assert candidate["seed_doi"] == "10.1000/seed"
    assert candidate["relation"] == "citation"
    assert candidate["relation_count"] == 3


def test_semantic_scholar_provider_record_is_preserved(tmp_path) -> None:
    run_dir = tmp_path / "semantic-scholar"
    run_dir.mkdir()
    provider = BibliographicRecord(
        source="Semantic Scholar",
        source_id="paper-1",
        title="Cider yeast ecology",
        abstract="A detailed official Semantic Scholar abstract about cider yeast ecology.",
        doi="10.1000/cider",
        url="https://www.semanticscholar.org/paper/paper-1",
    )
    (run_dir / "results.jsonl").write_text(
        json.dumps(
            {
                "engine": "semantic_scholar",
                "theme": "microbiologie",
                "url": provider.url,
                "title": provider.title,
                "doi": provider.doi,
                "provider_record": provider.model_dump(mode="json"),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    candidate = _load_candidates([run_dir])[0]

    assert candidate["provider_record"]["source"] == "Semantic Scholar"
    assert candidate["provider_record"]["abstract"].startswith("A detailed official")


def test_exact_doi_candidate_requires_provider_title_alignment(settings) -> None:
    class FakeCrossref:
        def lookup_dois(self, dois):
            assert dois == ["10.1000/cider"]
            return [
                BibliographicRecord(
                    source="Crossref",
                    source_id="10.1000/cider",
                    title="Cider fermentation kinetics in apple juice",
                    abstract="Yeast kinetics were measured during cider fermentation.",
                    doi="10.1000/cider",
                )
            ]

    accepted = _validate_candidate(
        {
            "candidate_key": "brave|https://doi.org/10.1000/cider",
            "engine": "brave",
            "theme": "microbiologie",
            "source_url": "https://doi.org/10.1000/cider",
            "title": "Cider fermentation kinetics in apple juice",
            "doi": "10.1000/cider",
        },
        FakeCrossref(),
        settings,
    )
    review = _validate_candidate(
        {
            "candidate_key": "brave|https://doi.org/10.1000/cider",
            "engine": "brave",
            "theme": "microbiologie",
            "source_url": "https://doi.org/10.1000/cider",
            "title": "Unrelated clinical trial",
            "doi": "10.1000/cider",
        },
        FakeCrossref(),
        settings,
    )

    assert accepted["status"] == "accepted"
    assert review["status"] == "review"


def test_exact_doi_validation_keeps_richer_semantic_scholar_abstract(settings) -> None:
    class FakeCrossref:
        def lookup_dois(self, dois):
            assert dois == ["10.1000/cider"]
            return [
                BibliographicRecord(
                    source="Crossref",
                    source_id="10.1000/cider",
                    title="Cider fermentation kinetics in apple juice",
                    abstract=None,
                    doi="10.1000/cider",
                )
            ]

    semantic = BibliographicRecord(
        source="Semantic Scholar",
        source_id="paper-1",
        title="Cider fermentation kinetics in apple juice",
        abstract="Detailed yeast kinetics were measured throughout cider fermentation.",
        doi="10.1000/cider",
        url="https://www.semanticscholar.org/paper/paper-1",
    )

    validation = _validate_candidate(
        {
            "candidate_key": "semantic_scholar|paper-1",
            "engine": "semantic_scholar",
            "theme": "microbiologie",
            "source_url": semantic.url,
            "title": semantic.title,
            "doi": semantic.doi,
            "provider_record": semantic.model_dump(mode="json"),
        },
        FakeCrossref(),
        settings,
    )

    assert validation["status"] == "accepted"
    assert validation["method"].endswith("semantic_scholar_enriched")
    assert validation["provider_record"]["source"] == "Semantic Scholar"
    assert validation["provider_record"]["abstract"].startswith("Detailed yeast kinetics")


def test_exact_doi_validation_keeps_richer_zenodo_abstract(settings) -> None:
    class FakeCrossref:
        def lookup_dois(self, dois):
            assert dois == ["10.5281/zenodo.42"]
            return [
                BibliographicRecord(
                    source="Crossref",
                    source_id="10.5281/zenodo.42",
                    title="Cider fermentation kinetics in apple juice",
                    abstract=None,
                    doi="10.5281/zenodo.42",
                )
            ]

    zenodo = BibliographicRecord(
        source="Zenodo",
        source_id="42",
        title="Cider fermentation kinetics in apple juice",
        abstract="Detailed yeast kinetics were measured throughout cider fermentation.",
        doi="10.5281/zenodo.42",
        url="https://zenodo.org/records/42",
    )

    validation = _validate_candidate(
        {
            "candidate_key": "zenodo|42",
            "engine": "zenodo",
            "theme": "microbiologie",
            "source_url": zenodo.url,
            "title": zenodo.title,
            "doi": zenodo.doi,
            "provider_record": zenodo.model_dump(mode="json"),
        },
        FakeCrossref(),
        settings,
    )

    assert validation["status"] == "accepted"
    assert validation["method"].endswith("zenodo_enriched")
    assert validation["provider_record"]["source"] == "Zenodo"
    assert validation["provider_record"]["abstract"].startswith("Detailed yeast kinetics")


def test_apply_validated_reassesses_provider_metadata_before_insertion(settings) -> None:
    active = settings.model_copy(deep=True)
    database = Database(active.paths.database_path)
    database.initialize()
    validation = {
        "candidate_key": "brave|https://example.test/paper",
        "engine": "brave",
        "theme": "microbiologie",
        "source_url": "https://example.test/paper",
        "status": "accepted",
        "provider_record": BibliographicRecord(
            source="Crossref",
            source_id="10.1000/cider",
            title="Cider yeast fermentation ecology",
            abstract=(
                "Saccharomyces yeast and lactic acid bacteria were monitored during apple cider "
                "fermentation."
            ),
            doi="10.1000/cider",
        ).model_dump(mode="json"),
    }

    report = _apply_validated(active, database, [validation])

    assert report["raw_candidates"] == 1
    assert report["accepted_records"] == 1
    with database.connect() as connection:
        source = connection.execute(
            "SELECT source FROM bibliographic_record_sources WHERE source LIKE 'brave%'"
        ).fetchone()
    assert source[0].startswith("brave web discovery validated by Crossref")


def test_apply_validated_labels_zenodo_staging_as_api(settings) -> None:
    active = settings.model_copy(deep=True)
    database = Database(active.paths.database_path)
    database.initialize()
    validation = {
        "candidate_key": "zenodo|42",
        "engine": "zenodo",
        "theme": "microbiologie",
        "source_url": "https://zenodo.org/records/42",
        "status": "accepted",
        "provider_record": BibliographicRecord(
            source="Zenodo",
            source_id="42",
            title="Cider yeast fermentation ecology",
            abstract=(
                "Saccharomyces yeast and lactic acid bacteria were monitored during apple cider "
                "fermentation."
            ),
            doi="10.5281/zenodo.42",
        ).model_dump(mode="json"),
    }

    report = _apply_validated(active, database, [validation])

    assert report["accepted_records"] == 1
    with database.connect() as connection:
        source = connection.execute(
            "SELECT source FROM bibliographic_record_sources WHERE source LIKE 'zenodo%'"
        ).fetchone()
    assert source[0].startswith("zenodo API discovery validated by Zenodo")


def test_apply_refuses_concurrent_harvest(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_runs (
                id, profile, state, themes, sources, per_source_limit,
                request_delay_seconds, started_at
            ) VALUES ('running-web-test', 'web_test', 'running', '{}', '[]', 1, 1.0,
                '2026-08-13T00:00:00+00:00')
            """
        )

    with pytest.raises(RuntimeError, match="harvest is active"):
        _require_no_running_harvest(database)
