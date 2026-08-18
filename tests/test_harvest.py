from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest

from app.database.sqlite import Database
from app.retrieval.lexical_search import LexicalQueryBuilder
from app.updates.harvest import (
    CIDER_BULK_QUERY_WAVES,
    CIDER_PILOT_THEMES,
    CIDER_QUERY_WAVES,
    AbstractBackfillReport,
    BibliographicHarvestStore,
    CiderAbstractBackfiller,
    CiderBulkHarvester,
    CiderPilotHarvester,
    HarvestNotDue,
    assess_cider_relevance,
    assess_cider_relevance_across_themes,
)
from app.updates.harvest_queries import (
    CIDER_EXPANDED_QUERY_WAVES,
    CIDER_MATERIAL_QUERY_WAVES,
    CIDER_MICROBIOLOGY_QUERY_WAVES,
    CIDER_SPECIALIZED_QUERY_WAVES,
)
from app.updates.models import BibliographicRecord
from app.updates.vector_index import (
    BibliographicHybridSearchService,
    BibliographicVectorIndex,
    expand_cider_query,
    index_bibliographic_abstracts,
)


def _active_settings(settings):
    active = settings.model_copy(deep=True)
    active.app.offline_mode = False
    active.app.allow_bibliographic_apis = True
    active.bibliographic.enabled = True
    active.harvest.enabled = True
    return active


def test_store_merges_sources_prefers_longer_abstract_and_indexes_fts(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["openalex", "crossref"],
    )
    store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="OpenAlex",
            source_id="W1",
            title="Cider yeast ecology",
            authors=["Ada Test"],
            abstract="Short fermentation abstract.",
            publication_year=2024,
            doi="10.1000/cider",
        ),
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/cider",
            title="Cider yeast ecology",
            authors=["Bob Demo"],
            abstract=("A longer fermentation abstract about yeast ecology in cider."),
            publication_year=2024,
            doi="10.1000/cider",
        ),
    )
    completed = datetime.now(UTC)
    unique_count, abstract_count, accepted_count, accepted_abstract_count = store.finish_run(
        run_id=run_id,
        state="completed",
        raw_record_count=2,
        errors=[],
        completed_at=completed,
    )

    expression = LexicalQueryBuilder(active).build("fermentation yeast").fts5_expression
    rows = store.search(expression)
    stored = store.records_by_ids([record_id])[record_id]

    assert unique_count == 1
    assert abstract_count == 1
    assert accepted_count == 1
    assert accepted_abstract_count == 1
    assert len(rows) == 1
    assert stored["abstract"].startswith("A longer")
    assert "Ada Test" in stored["authors"]
    assert "Bob Demo" in stored["authors"]
    assert set(str(stored["sources"]).split(",")) == {"OpenAlex", "Crossref"}
    assert stored["embedding_status"] == "pending"
    assert store.statistics()["records"] == 1
    assert not store.is_due(active, now=completed)


def test_store_recovers_interrupted_run_without_discarding_hits(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["openalex"],
    )
    store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="OpenAlex",
            source_id="W-INTERRUPTED",
            title="Cider yeast recovery",
            authors=["Ada Test"],
            abstract="A relevant abstract about cider fermentation yeast.",
            publication_year=2024,
            doi="10.1000/interrupted-cider",
        ),
    )

    report = store.recover_interrupted_run(
        run_id=run_id,
        reason="collector process exited before its final acknowledgement",
        completed_at=datetime.now(UTC),
    )

    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT state, unique_record_count, accepted_abstract_count, errors
            FROM bibliographic_harvest_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    assert report["state"] == "partial"
    assert report["unique_record_count"] == 1
    assert row["state"] == "partial"
    assert row["unique_record_count"] == 1
    assert row["accepted_abstract_count"] == 1
    assert json.loads(row["errors"])[-1]["error_type"] == "InterruptedHarvestRecovered"


def test_store_refuses_to_recover_a_completed_run(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["openalex"],
    )
    completed_at = datetime.now(UTC)
    store.finish_run(
        run_id=run_id,
        state="completed",
        raw_record_count=0,
        errors=[],
        completed_at=completed_at,
    )

    with pytest.raises(ValueError, match="is not running"):
        store.recover_interrupted_run(
            run_id=run_id,
            reason="must not overwrite a completed run",
            completed_at=completed_at,
        )


def test_store_uses_doi_before_title_fallback_and_browses_all_statuses(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["openalex", "crossref"],
    )
    first_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="OpenAlex",
            source_id="W-DOI-A",
            title="Cider yeast ecology",
            abstract="Cider yeast fermentation and microbial ecology.",
            authors=["Pascal Poupard"],
            publication_year=2024,
            doi="10.1000/doi-a",
        ),
    )
    second_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=2,
        record=BibliographicRecord(
            source="Crossref",
            source_id="DOI-B",
            title="Cider yeast ecology",
            abstract="Cider yeast fermentation with a distinct DOI.",
            authors=["Poupard P"],
            publication_year=2024,
            doi="10.1000/doi-b",
        ),
    )
    merged_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=3,
        record=BibliographicRecord(
            source="Crossref",
            source_id="DOI-A",
            title="A richer title supplied later",
            abstract="A longer cider yeast fermentation abstract about microbial ecology.",
            publication_year=2024,
            doi="10.1000/doi-a",
        ),
    )

    assert first_id != second_id
    assert merged_id == first_id
    result = store.browse_records(
        query="doi-a",
        statuses=["accepted"],
        source="Crossref",
        has_abstract=True,
    )
    assert result["total"] == 1
    assert result["records"][0]["id"] == first_id
    author_matches = store.browse_records(query="Poupard, Pascal")["records"]
    assert {record["id"] for record in author_matches} == {first_id, second_id}
    assert store.browse_records(query="OpenAlex, 2024")["records"][0]["id"] == first_id
    assert store.browse_records(query="W-DOI-A microbiologie")["records"][0]["id"] == first_id
    assert store.search_metadata("Poupard 2024")[0]["id"] == first_id
    assert store.browse_filter_options() == {
        "themes": ["microbiologie"],
        "sources": ["Crossref", "OpenAlex"],
    }

    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute(
            "UPDATE bibliographic_records SET doi = ? WHERE id = ?",
            ("10.1000/DOI-A", second_id),
        )


def test_store_merges_normalized_title_when_doi_enrichment_arrives(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider bacteria"},
        sources=["europe_pmc"],
    )
    provisional_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="Europe PMC",
            source_id="MED-1",
            title="Biogenic amines in French ciders",
            abstract="Cider bacteria and biogenic amines during fermentation.",
            publication_year=2011,
        ),
    )

    enriched_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=2,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/biogenic",
            title="Biogenic amines in French ciders.",
            abstract="Cider bacteria and biogenic amines during fermentation.",
            publication_year=2011,
            doi="10.1000/biogenic",
        ),
    )

    assert enriched_id == provisional_id
    stored = store.records_by_ids([provisional_id])[provisional_id]
    assert stored["doi"] == "10.1000/biogenic"
    assert store.browse_records()["total"] == 1


def test_store_deduplicates_doi_less_titles_through_the_canonical_key(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider bacteria"},
        sources=["Aureli"],
    )
    first_id, duplicate_id = store.upsert_hits(
        run_id=run_id,
        hits=[
            (
                "microbiologie",
                1,
                BibliographicRecord(
                    source="Aureli",
                    source_id="PNX-1",
                    title="Biogenic amines in French ciders",
                    abstract="Cider bacteria and biogenic amines during fermentation.",
                    publication_year=2011,
                ),
            ),
            (
                "microbiologie",
                2,
                BibliographicRecord(
                    source="Aureli",
                    source_id="PNX-2",
                    title="Biogenic amines in French ciders.",
                    abstract="A longer abstract on cider bacteria and biogenic amines.",
                    publication_year=2011,
                ),
            ),
        ],
    )

    assert duplicate_id == first_id
    assert store.browse_records()["total"] == 1


def test_store_merges_legacy_doi_enrichment_duplicates(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider bacteria"},
        sources=["crossref", "europe_pmc"],
    )
    survivor_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=2,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/biogenic",
            title="Biogenic amines in French ciders.",
            abstract="Short cider bacteria abstract.",
            authors=["Hugues Guichard"],
            publication_year=2011,
            doi="10.1000/biogenic",
        ),
    )
    duplicate_id = "legacy-provisional-record"
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors,
                publication_year, content_hash, embedding_status,
                relevance_status, relevance_score, relevance_theme
            ) VALUES (?, ?, NULL, ?, ?, ?, 2011, ?, 'pending',
                'accepted', 0.95, 'microbiologie')
            """,
            (
                duplicate_id,
                "title:legacy-biogenic",
                "Biogenic amines in French ciders",
                "A much longer abstract about cider bacteria and fermentation.",
                '["Rémi Bauduin"]',
                "0" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO bibliographic_record_sources (record_id, source, source_id)
            VALUES (?, 'Europe PMC', 'MED-1')
            """,
            (duplicate_id,),
        )
        connection.execute(
            """
            INSERT INTO bibliographic_harvest_hits (
                run_id, theme, record_id, source, rank,
                relevance_status, relevance_score, relevance_reason
            ) VALUES (?, 'microbiologie', ?, 'Europe PMC', 1,
                'accepted', 0.95, 'strong cider evidence')
            """,
            (run_id, duplicate_id),
        )

    assert store.merge_doi_enrichment_duplicates() == [duplicate_id]

    stored = store.records_by_ids([survivor_id])[survivor_id]
    assert "A much longer abstract" in stored["abstract"]
    assert "Hugues Guichard" in stored["authors"]
    assert "Rémi Bauduin" in stored["authors"]
    assert set(str(stored["sources"]).split(",")) == {"Crossref", "Europe PMC"}
    assert store.records_by_ids([duplicate_id]) == {}
    assert store.browse_records()["total"] == 1


def test_rejected_records_are_archived_with_doi_and_title_before_purge(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["crossref"],
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/irrelevant",
            title="Unrelated wheat protein study",
            abstract="A cereal research abstract without fermented apple beverages.",
            doi="10.1000/irrelevant",
        ),
    )

    archived = store.archive_rejected_records()
    deleted = store.purge_archived_rejected_records([record_id])

    assert archived[0]["doi"] == "10.1000/irrelevant"
    assert archived[0]["title"] == "Unrelated wheat protein study"
    assert deleted == 1
    assert store.browse_records(statuses=["rejected"])["total"] == 0
    assert store.doi_exclusions.is_excluded("10.1000/irrelevant")
    assert (
        store.upsert_hit(
            run_id=run_id,
            theme="microbiologie",
            rank=2,
            record=BibliographicRecord(
                source="Crossref",
                source_id="10.1000/irrelevant-again",
                title="Unrelated wheat protein study",
                abstract="A cereal research abstract without fermented apple beverages.",
                doi="10.1000/irrelevant",
            ),
        )
        is None
    )
    assert store.archive_statistics() == {
        "archive_total": 1,
        "remaining_rejected_records": 0,
    }


def test_abstractless_records_are_rejected_and_purged_after_enrichment(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["crossref"],
    )
    missing_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/no-abstract",
            title="Cider yeast ecology and fermentation",
            doi="10.1000/no-abstract",
        ),
    )
    usable_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=2,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/with-abstract",
            title="Cider yeast population during fermentation",
            abstract="Yeast succession controls cider fermentation and sensory quality.",
            doi="10.1000/with-abstract",
        ),
    )
    store.finish_run(
        run_id=run_id,
        state="completed",
        raw_record_count=2,
        errors=[],
        completed_at=datetime.now(UTC),
    )

    rejected = store.reject_abstractless_records()
    archived = store.archive_rejected_records()
    deleted = store.purge_archived_rejected_records([missing_id])

    assert rejected == 1
    assert deleted == 1
    assert archived[0]["original_record_id"] == missing_id
    assert archived[0]["doi"] == "10.1000/no-abstract"
    assert "Abstract unavailable" in str(archived[0]["relevance_reason"])
    assert store.reject_abstractless_records() == 0
    assert store.records_by_ids([missing_id]) == {}
    assert store.records_by_ids([usable_id])[usable_id]["abstract"] is not None
    statistics = store.statistics()
    assert statistics["records"] == statistics["abstracts"] == 1
    assert statistics["stored_records"] == statistics["stored_abstracts"] == 1
    assert statistics["review"] == statistics["quarantined"] == 0
    assert statistics["latest_run"]["accepted_record_count"] == 1
    assert statistics["latest_run"]["accepted_abstract_count"] == 1


def test_abstractless_cleanup_preserves_an_explicit_manual_admission(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["local"],
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=BibliographicRecord(
            source="local",
            source_id="manual-full-text",
            title="Cider yeast ecology full-text record",
            doi="10.1000/manual-full-text",
        ),
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE bibliographic_records SET manual_decision = 'accepted' WHERE id = ?",
            (record_id,),
        )

    assert store.reject_abstractless_records() == 0
    assert store.records_by_ids([record_id])[record_id]["relevance_status"] == "accepted"


def test_global_doi_less_review_excludes_only_automatic_abstracts(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"aromes_procede": "cider"},
        sources=["local"],
    )
    automatic_id = store.upsert_hit(
        run_id=run_id,
        theme="aromes_procede",
        rank=1,
        record=BibliographicRecord(
            source="local",
            source_id="automatic-doi-less",
            title="Cider sensory fermentation quality",
            abstract="Cider fermentation changed volatile aroma and sensory quality.",
        ),
    )

    assert store.review_doi_less_abstracts() == 1
    with closing(database.connect()) as connection:
        status = connection.execute(
            "SELECT relevance_status FROM bibliographic_records WHERE id = ?",
            (automatic_id,),
        ).fetchone()[0]
    assert status == "review"


def test_run_evidence_cleanup_is_scoped_and_preserves_review_candidates(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    first_run, _ = store.start_run(
        active,
        themes={"aromes_procede": "cider"},
        sources=["Aureli"],
    )
    second_run, _ = store.start_run(
        active,
        themes={"aromes_procede": "cider"},
        sources=["Aureli"],
    )
    missing_abstract_id = store.upsert_hit(
        run_id=first_run,
        theme="aromes_procede",
        rank=1,
        record=BibliographicRecord(
            source="Aureli",
            source_id="missing-abstract",
            title="Cider fermentation process and quality",
            doi="10.1000/missing-abstract",
        ),
    )
    missing_doi_id = store.upsert_hit(
        run_id=first_run,
        theme="aromes_procede",
        rank=2,
        record=BibliographicRecord(
            source="Aureli",
            source_id="missing-doi",
            title="Apple cider sensory fermentation quality study",
            abstract="Cider fermentation controls aroma and sensory quality.",
        ),
    )
    other_run_id = store.upsert_hit(
        run_id=second_run,
        theme="aromes_procede",
        rank=1,
        record=BibliographicRecord(
            source="Aureli",
            source_id="other-run",
            title="Cider fermentation process in another study",
            doi="10.1000/other-run",
        ),
    )

    assert store.reject_run_abstractless_records(first_run) == 1
    assert store.review_run_doi_less_abstracts(first_run) == 1
    with closing(database.connect()) as connection:
        statuses = {
            str(row["id"]): str(row["relevance_status"])
            for row in connection.execute("SELECT id, relevance_status FROM bibliographic_records")
        }

    assert statuses[missing_abstract_id] == "rejected"
    assert statuses[missing_doi_id] == "review"
    assert statuses[other_run_id] == "accepted"


def test_cross_theme_assessment_selects_the_strongest_scientific_theme() -> None:
    record = BibliographicRecord(
        source="Aureli",
        source_id="theme-selection",
        title="Cider fermentation with non-Saccharomyces yeasts",
        abstract="Microbial succession shapes aroma during alcoholic fermentation.",
        doi="10.1000/theme-selection",
    )

    theme, assessment = assess_cider_relevance_across_themes(record)

    assert theme == "microbiologie"
    assert assessment.status == "accepted"
    assert assessment.score == 1.0


def test_rejected_archive_preserves_a_historical_doi_when_new_hit_omits_it(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "cider yeast"},
        sources=["crossref"],
    )
    record = BibliographicRecord(
        source="Crossref",
        source_id="historical-doi",
        title="Unrelated cereal automation study",
        abstract="No apple beverage context.",
        doi="10.1000/historical",
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="microbiologie",
        rank=1,
        record=record,
    )
    store.archive_rejected_records()
    with database.transaction() as connection:
        connection.execute(
            "UPDATE bibliographic_records SET doi = NULL WHERE id = ?",
            (record_id,),
        )

    archived = store.archive_rejected_records()
    deleted = store.purge_archived_rejected_records([record_id])

    assert archived[0]["doi"] is None
    assert deleted == 1
    with closing(database.connect()) as connection:
        stored_doi = connection.execute(
            "SELECT doi FROM rejected_bibliographic_archive WHERE original_record_id = ?",
            (record_id,),
        ).fetchone()[0]
    assert stored_doi == "10.1000/historical"


@pytest.mark.parametrize(
    ("title", "abstract"),
    [
        (
            "CIDEr: Consensus-based image description evaluation",
            "A computer vision metric evaluates generated image captions.",
        ),
        (
            "Cider: An Event-Driven Continuous Integration Server",
            "A modular event-driven server integrates development tools through a "
            "communication platform and governs the development process.",
        ),
        (
            "Analysis of aroma compounds of commercial cider vinegars",
            "Electronic nose analysis of apple cider vinegars.",
        ),
        (
            "Le vinaigre de cidre de pomme : propriétés physicochimiques",
            "Identification de molécules bioactives et applications thérapeutiques.",
        ),
        (
            "Consumer willingness to pay for locally produced hard cider",
            "A socioeconomic market survey of cider consumers.",
        ),
        (
            "A Whig parade during the Hard Cider Campaign",
            "A historical study of the nineteenth-century political campaign.",
        ),
        (
            "Effects of yeast on sparkling pear cider",
            "Fermentation and sensory properties of perry made from pear juice.",
        ),
        (
            "Apple cider vinegar and blood lipids in rats",
            "A cholesterol study in rats.",
        ),
        (
            "Performance analysis of an unsignalized intersection using SIDRA software",
            "A traffic engineering case study using intersection simulation software.",
        ),
        (
            "Formulation of antiacne cream containing freeze dried cashew apple juice",
            "A topical cream was evaluated for acne treatment.",
        ),
        (
            "Optimization of pectinase in the clarification of sugar apple juice",
            "Annona squamosa juice was clarified with commercial enzymes.",
        ),
        (
            "Effects of Fermented Apple-Ring Seed Meal Diets on Fish",
            "Faidherbia albida seed meal changed biochemical parameters.",
        ),
        (
            "Effect of different level of yeast on properties of Guava cider",
            "Guava juice was fermented into a fruit cider.",
        ),
        (
            "Impact of fermentation on a Lychee–Pineapple functional cider",
            "The non-apple fruit beverage was evaluated for bioactive compounds.",
        ),
        (
            "Khảo sát các điều kiện lên men cider lêkima",
            "Canistel juice was fermented under controlled biochemical conditions.",
        ),
        (
            "A Climate Intervention Dynamical Emulator (CIDER) for scenario exploration",
            "The emulator maps climate intervention scenario space.",
        ),
        (
            "CIDER: Boosting Memory-Disaggregated Key-Value Stores",
            "A database system uses pessimistic synchronization.",
        ),
        (
            "GUI-CIDER: Mid-training GUI Agents via Causal Internalization",
            "Software agents learn from density-aware exemplars.",
        ),
        (
            "Cider as a Sign: Interpretations of Shaker Spirits and Spirituality",
            "A historical analysis of religious communities.",
        ),
        (
            "Consumption profiles, consumer experience and quality claims of cider",
            "A marketing analysis studies consumer preferences.",
        ),
        (
            "Stability of topical formulations enriched with apple pomace extract",
            "Cosmetic emulsions were assessed for topical skin application.",
        ),
        (
            "Asturian cider in Madrid? Linguistic identity and multilingual signage",
            "Restaurant signs were studied as markers of regional identity.",
        ),
        (
            "The uniqueness of one apple: producer perspectives of hard cider",
            "Interviews examined producer identities and business perspectives.",
        ),
        (
            "Implicit reaction vs explicit emotional response: PDO apple cider",
            "Consumers reacted to protected designation of origin labels.",
        ),
        (
            "Natural fermentation of sap from the cider gum Eucalyptus gunnii",
            "Microbial communities in fermented tree sap were characterized.",
        ),
        (
            "Cosmeceutical potency of functional ripe buni cider",
            "The non-apple drink was evaluated in a topical cosmetic application.",
        ),
        (
            "Hospital admissions for alcohol-related liver disease after cider regulation",
            "A retrospective clinical cohort examined heavy drinkers.",
        ),
        (
            "Back Cover: Determination of sugars in hard ciders and apple juice",
            "This item reproduces the journal back cover.",
        ),
        (
            "Forecasting purchasing quantity of apples for juice drink production",
            "A company procurement forecast estimated purchasing quantity.",
        ),
        (
            "Research and extension needs of U.S. hard cider producers",
            "A producer survey described business needs.",
        ),
        (
            "Le renouvellement d’un cluster basé sur la proximité organisée",
            "Une étude économique porte sur un cluster cidricole.",
        ),
        (
            "Modelling detrital cosmogenic nuclides in CIDRE V2.0",
            "The landscape evolution model simulates erosion and cosmogenic nuclides.",
        ),
        (
            "Broken bones and apple brandy during the COVID-19 pandemic",
            "General practitioners discussed resilience with at-risk patients.",
        ),
        (
            "UV Light Reveals Jurassic Shell Colour Patterns in Calvados, France",
            "An archaeological fossil site in the Calvados department was studied.",
        ),
        (
            "Diversificacion productiva cafe-plantas ornamentales en La Sidra",
            "A regional development study in La Sidra examined ornamental plants.",
        ),
        (
            "Making good cider out of bad apples: signaling boosts cooperation",
            "A game-theory study examined expectations and would-be free riders.",
        ),
        (
            "Puzzles: Cider in Your Ear, Continuing Dilemma, and More",
            "This economics column presents several recreational puzzles.",
        ),
    ],
)
def test_relevance_gate_rejects_broad_query_false_positives(title, abstract) -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id=title,
            title=title,
            abstract=abstract,
            publication_year=2024,
        ),
        "aromes_procede",
    )

    assert assessment.status == "rejected"


def test_relevance_gate_keeps_a_concise_legitimate_cider_article() -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="legitimate",
            title="Characterization of cider by its hydrophobic protein profile",
            abstract="Apple cider proteins influence foam quality and processing.",
            publication_year=2024,
        ),
        "proteines",
    )

    assert assessment.status == "accepted"


def test_relevance_gate_keeps_calvados_with_explicit_spirit_context() -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="calvados-spirit",
            title="Volatile aroma compounds during oak ageing of Calvados",
            abstract="Apple brandy distillation and barrel maturation changed ester composition.",
            publication_year=2024,
        ),
        "calvados_eau_vie",
    )

    assert assessment.status == "accepted"


@pytest.mark.parametrize(
    ("title", "abstract", "theme"),
    [
        (
            "Inactivation of Cryptosporidium parvum oocysts in cider by flash pasteurization",
            "Flash pasteurization improved the microbiological safety of fermented cider.",
            "jus_pomme",
        ),
        (
            "Tetrad-forming Cocci in Ciders",
            "Bacterial isolates from cider fermentation were characterized.",
            "microbiologie",
        ),
        (
            "Filtration of cider",
            "The membrane filtration process clarified the finished beverage.",
            "jus_pomme",
        ),
        (
            "Determination of monosaccharides in cider by liquid chromatography",
            "The HPLC method quantified sugars in finished ciders.",
            "biochimie",
        ),
        (
            "Influence of Fatty Acids on Foaming Properties of Cider",
            "Fatty-acid composition changed the foam properties of cider.",
            "proteines",
        ),
        (
            "Bioconversion of apple pomace with black soldier fly larvae",
            "Larval bioconversion characterized the processing of apple pomace.",
            "microbiologie",
        ),
        (
            "Essai de microfiltration du cidre",
            "La microfiltration a été appliquée au cidre fini.",
            "jus_pomme",
        ),
        (
            "Control de plagas del manzano de sidra por aves silvestres",
            "El control biológico protege los manzanos destinados a la sidra.",
            "jus_pomme",
        ),
        (
            "Valorización biotecnológica de residuos de elaboración de sidra de manzana",
            "Los residuos de Malus domestica se transformaron mediante un proceso biotecnológico.",
            "aromes_procede",
        ),
        (
            "Potencial de una variedad para el procesamiento de suco e vinho seco de maçã",
            "O suco clarificado e o vinho seco de maçã foram caracterizados.",
            "jus_pomme",
        ),
    ],
)
def test_relevance_gate_keeps_concise_technical_cider_titles(
    title: str, abstract: str, theme: str
) -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id=title,
            title=title,
            abstract=abstract,
            publication_year=2024,
        ),
        theme,
    )

    assert assessment.status == "accepted"


def test_relevance_gate_keeps_an_apple_and_pear_cider_blend() -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="apple-pear-blend",
            title="Phenolic composition of apple and pear cider blends",
            abstract="Apple cider blended with pear juice changed tannin composition.",
            publication_year=2024,
        ),
        "polyphenols",
    )

    assert assessment.status == "accepted"


@pytest.mark.parametrize(
    "title",
    [
        "Analysis of regional ciders with pear addition",
        "Effects of blueberry extracts addition on antioxidant properties of cider",
    ],
)
def test_relevance_gate_keeps_non_apple_fruit_additions_to_cider(title: str) -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id=title,
            title=title,
            abstract="The addition was evaluated in fermented cider for phenolic quality.",
            publication_year=2024,
        ),
        "polyphenols",
    )

    assert assessment.status == "accepted"


def test_relevance_gate_distinguishes_water_comma_apple_from_water_apple() -> None:
    apple_juice = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="enumeration",
            title="Permeabilization in Water, Apple and Carrot Juice",
            abstract="The treatment was assessed during apple juice and cider processing.",
            publication_year=2024,
        ),
        "jus_pomme",
    )
    tropical_fruit = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="tropical-fruit",
            title="Physicochemical properties of water apple juice",
            abstract="Syzygium aqueum fruit juice was characterized.",
            publication_year=2024,
        ),
        "jus_pomme",
    )

    assert apple_juice.status != "rejected"
    assert tropical_fruit.status == "rejected"


def test_harvester_is_bounded_by_free_openalex_budget_and_weekly_cadence(
    settings, monkeypatch
) -> None:
    active = _active_settings(settings)
    active.bibliographic.sources = ["openalex"]
    active.harvest.per_source_limit = 1
    database = Database(active.paths.database_path)
    database.initialize()

    class FakeOpenAlex:
        def __init__(self, _settings) -> None:
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def rate_limit_status(self):
            return {"daily_remaining_usd": 0.99 - self.calls * 0.001}

        def search(self, query: str, _limit: int):
            self.calls += 1
            return [
                BibliographicRecord(
                    source="OpenAlex",
                    source_id=f"W{self.calls}",
                    title=f"Cider pilot {self.calls}",
                    abstract=f"Abstract for {query}",
                    doi=f"10.1000/cider-pilot-{self.calls}",
                )
            ]

    monkeypatch.setattr("app.updates.harvest.OpenAlexClient", FakeOpenAlex)
    monkeypatch.setattr("app.updates.harvest.CLIENTS", {"openalex": FakeOpenAlex})
    harvester = CiderPilotHarvester(active, database)

    report = harvester.run(force=True)

    assert report.state == "completed"
    assert report.raw_record_count == len(CIDER_PILOT_THEMES)
    assert report.unique_record_count == len(CIDER_PILOT_THEMES)
    assert report.abstract_record_count == len(CIDER_PILOT_THEMES)
    assert report.accepted_record_count == len(CIDER_PILOT_THEMES)
    assert report.accepted_abstract_count == len(CIDER_PILOT_THEMES)
    assert report.openalex_daily_remaining_before_usd == pytest.approx(0.99)
    assert report.openalex_daily_remaining_after_usd == pytest.approx(0.982)
    with pytest.raises(HarvestNotDue):
        harvester.run()


def test_harvested_abstracts_are_indexed_in_a_separate_vector_collection(
    settings,
) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"microbiologie": "yeast", "polyphenols": "phenolics"},
        sources=["openalex"],
    )
    for rank, (title, abstract, doi) in enumerate(
        [
            (
                "Yeast ecology in cider",
                "Yeast fermentation controls cider aroma.",
                "10.1000/yeast",
            ),
            (
                "Polyphenols in apple juice",
                "Phenolic compounds influence oxidation and colour.",
                "10.1000/phenolics",
            ),
        ],
        start=1,
    ):
        store.upsert_hit(
            run_id=run_id,
            theme="microbiologie" if rank == 1 else "polyphenols",
            rank=rank,
            record=BibliographicRecord(
                source="OpenAlex",
                source_id=f"W{rank}",
                title=title,
                abstract=abstract,
                doi=doi,
            ),
        )

    class FakeBackend:
        model_name = active.embeddings.model_name
        dimension = 2

        def encode_documents(self, texts):
            return [[1.0, 0.0] if "Yeast" in text else [0.0, 1.0] for text in texts]

        def encode_queries(self, _texts):
            return [[1.0, 0.0]]

        def close(self):
            pass

    backend = FakeBackend()
    report = index_bibliographic_abstracts(active, store, backend, close_backend=False)
    index = BibliographicVectorIndex(active)
    service = BibliographicHybridSearchService(active, store, backend, index)
    try:
        response = service.search("yeast fermentation", limit=2)
        assert report.records_indexed == 2
        assert index.count() == 2
        assert response.results[0].doi == "10.1000/yeast"
        assert response.results[0].vector_rank == 1
        assert response.results[0].sources == ["OpenAlex"]
        assert response.lexical_candidate_count >= 1
        assert response.dense_candidate_count == 2
        assert response.rrf_unique_candidate_count == 2
    finally:
        service.close()


def test_harvest_rotates_queries_then_pages_results(settings, monkeypatch) -> None:
    active = _active_settings(settings)
    active.bibliographic.sources = ["crossref"]
    active.harvest.per_source_limit = 2
    database = Database(active.paths.database_path)
    database.initialize()
    observed: list[tuple[str, int]] = []

    class FakeCrossref:
        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def search(self, query: str, _limit: int, *, offset: int = 0):
            observed.append((query, offset))
            return []

    monkeypatch.setattr("app.updates.harvest.CLIENTS", {"crossref": FakeCrossref})
    harvester = CiderPilotHarvester(active, database)

    reports = [harvester.run(force=True) for _ in range(len(CIDER_QUERY_WAVES) + 1)]

    assert [report.query_wave for report in reports] == [0, 1, 2, 3, 0]
    assert [report.result_offset for report in reports] == [0, 0, 0, 0, 2]
    calls_per_run = len(CIDER_PILOT_THEMES)
    assert all(offset == 0 for _, offset in observed[: calls_per_run * 4])
    assert all(offset == 2 for _, offset in observed[calls_per_run * 4 :])

    active.harvest.profile = "cider_shifted_pages"
    shifted = CiderPilotHarvester(active, database, start_page=3).run(force=True)
    assert shifted.result_offset == 6


def test_bulk_harvest_stops_when_new_accepted_abstract_target_is_reached(
    settings, monkeypatch
) -> None:
    active = _active_settings(settings)
    active.bibliographic.sources = ["crossref"]
    database = Database(active.paths.database_path)
    database.initialize()

    class FakeCrossref:
        calls = 0

        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def search(self, query: str, limit: int, *, offset: int = 0):
            records = []
            for position in range(limit):
                type(self).calls += 1
                records.append(
                    BibliographicRecord(
                        source="Crossref",
                        source_id=f"bulk-{type(self).calls}",
                        title=f"Cider fermentation study {type(self).calls}",
                        abstract=f"Cider {query} fermentation processing results.",
                        doi=f"10.1000/bulk-{offset}-{type(self).calls}-{position}",
                    )
                )
            return records

    monkeypatch.setattr("app.updates.harvest.CLIENTS", {"crossref": FakeCrossref})

    report = CiderBulkHarvester(active, database).run(
        target_new_accepted_abstracts=2,
        page_size=2,
        max_runs=3,
        profile="cider_design_bulk_test",
    )

    assert report.target_reached
    assert report.stop_reason == "target_reached"
    assert report.new_accepted_abstracts >= 2
    assert len(report.harvest_runs) == 1
    assert report.harvest_runs[0].themes == list(CIDER_BULK_QUERY_WAVES[0])


def test_bulk_harvest_stops_after_two_completely_failed_provider_runs(
    settings, monkeypatch
) -> None:
    active = _active_settings(settings)
    active.bibliographic.sources = ["crossref"]
    database = Database(active.paths.database_path)
    database.initialize()

    class ForbiddenCrossref:
        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def search(self, query: str, limit: int, *, offset: int = 0):
            raise RuntimeError("provider returned HTTP 403")

    monkeypatch.setattr("app.updates.harvest.CLIENTS", {"crossref": ForbiddenCrossref})

    report = CiderBulkHarvester(active, database).run(
        target_new_accepted_abstracts=10,
        page_size=2,
        max_runs=5,
        profile="cider_provider_failure_test",
    )

    assert report.stop_reason == "no_progress"
    assert report.new_accepted_abstracts == 0
    assert len(report.harvest_runs) == 2
    assert report.backfill_runs == []
    assert all(run.raw_record_count == 0 and run.errors for run in report.harvest_runs)


def test_bulk_harvest_suspends_backfill_after_two_zero_yield_batches(settings, monkeypatch) -> None:
    active = _active_settings(settings)
    active.bibliographic.sources = ["crossref"]
    database = Database(active.paths.database_path)
    database.initialize()

    class AbstractlessCrossref:
        calls = 0

        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def search(self, query: str, limit: int, *, offset: int = 0):
            type(self).calls += 1
            value = type(self).calls
            return [
                BibliographicRecord(
                    source="Crossref",
                    source_id=f"abstractless-{value}",
                    title=f"Cider fermentation study {value}",
                    doi=f"10.1000/abstractless-{value}",
                )
            ]

    class ZeroYieldBackfiller:
        calls = 0

        def __init__(self, _settings, _database) -> None:
            pass

        def run(self, *, limit: int = 100) -> AbstractBackfillReport:
            type(self).calls += 1
            return AbstractBackfillReport(
                run_id=None,
                state="completed",
                candidates=limit,
                matched_records=limit,
                abstracts_added=0,
                errors=[],
            )

    monkeypatch.setattr("app.updates.harvest.CLIENTS", {"crossref": AbstractlessCrossref})
    monkeypatch.setattr("app.updates.harvest.CiderAbstractBackfiller", ZeroYieldBackfiller)

    report = CiderBulkHarvester(active, database).run(
        target_new_accepted_abstracts=10,
        page_size=1,
        max_runs=5,
        profile="cider_backfill_saturation_test",
    )

    assert report.stop_reason == "max_runs"
    assert len(report.harvest_runs) == 5
    assert len(report.backfill_runs) == 2
    assert ZeroYieldBackfiller.calls == 2


def test_backfill_adds_openalex_abstract_to_an_accepted_doi(settings, monkeypatch) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"proteines": "cider nitrogen"},
        sources=["crossref"],
    )
    record_id = store.upsert_hit(
        run_id=run_id,
        theme="proteines",
        rank=1,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/nitrogen",
            title="Cider nitrogen and amino acids during fermentation",
            doi="10.1000/nitrogen",
        ),
    )

    class FakeOpenAlex:
        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def rate_limit_status(self):
            return {
                "daily_remaining_usd": 1.0,
                "endpoint_costs_usd": {"list": 0.0001},
            }

        def lookup_dois(self, dois):
            assert dois == ["10.1000/nitrogen"]
            return [
                BibliographicRecord(
                    source="OpenAlex",
                    source_id="W-NITROGEN",
                    title="Cider nitrogen and amino acids during fermentation",
                    abstract="Yeast assimilable nitrogen controls cider fermentation kinetics.",
                    doi="10.1000/nitrogen",
                )
            ]

    monkeypatch.setattr("app.updates.harvest.OpenAlexClient", FakeOpenAlex)

    report = CiderAbstractBackfiller(active, database).run()
    stored = store.records_by_ids([record_id])[record_id]

    assert report.state == "completed"
    assert report.candidates == 1
    assert report.matched_records == 1
    assert report.abstracts_added == 1
    assert stored["embedding_status"] == "pending"
    assert "assimilable nitrogen" in stored["abstract"]


def test_backfill_miss_is_not_retried_for_thirty_days(settings, monkeypatch) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()
    store = BibliographicHarvestStore(database)
    run_id, _ = store.start_run(
        active,
        themes={"polyphenols": "cider tannins"},
        sources=["crossref"],
    )
    store.upsert_hit(
        run_id=run_id,
        theme="polyphenols",
        rank=1,
        record=BibliographicRecord(
            source="Crossref",
            source_id="10.1000/tannins",
            title="Polyphenols and tannins in cider",
            doi="10.1000/tannins",
        ),
    )

    class EmptyOpenAlex:
        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def rate_limit_status(self):
            return {"daily_remaining_usd": 1.0}

        def lookup_dois(self, _dois):
            return []

    monkeypatch.setattr("app.updates.harvest.OpenAlexClient", EmptyOpenAlex)
    backfiller = CiderAbstractBackfiller(active, database)

    first = backfiller.run()
    second = backfiller.run()

    assert first.state == "completed"
    assert first.candidates == 1
    assert first.matched_records == 0
    assert second.state == "skipped"
    assert second.candidates == 0


def test_french_cider_query_is_expanded_with_local_bilingual_vocabulary() -> None:
    expanded = expand_cider_query("azote protéines fermentation du cidre")

    assert "yeast assimilable nitrogen" in expanded
    assert "amino acids" in expanded
    assert "aroma volatile sensory" in expanded


def test_relevance_gate_accepts_cider_science_and_quarantines_noise() -> None:
    accepted = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="1",
            title="Yeast ecology and malolactic bacteria in cider fermentation",
            abstract="Microbial succession controls cider quality.",
        ),
        "microbiologie",
    )
    rejected = assess_cider_relevance(
        BibliographicRecord(
            source="test",
            source_id="2",
            title="Instrumentation at the leading edge of proteomics",
            abstract="A general overview of mass spectrometry instrumentation.",
        ),
        "proteines",
    )

    assert accepted.status == "accepted"
    assert accepted.score >= 0.7
    assert rejected.status == "rejected"
    assert rejected.score < 0.45


def test_expanded_query_waves_cover_every_theme_without_duplicates() -> None:
    assert len(CIDER_EXPANDED_QUERY_WAVES) == 12
    assert all(set(wave) == set(CIDER_PILOT_THEMES) for wave in CIDER_EXPANDED_QUERY_WAVES)
    queries = [query for wave in CIDER_EXPANDED_QUERY_WAVES for query in wave.values()]
    assert len(queries) == len(set(queries))

    assert len(CIDER_SPECIALIZED_QUERY_WAVES) == 8
    assert all(set(wave) == set(CIDER_PILOT_THEMES) for wave in CIDER_SPECIALIZED_QUERY_WAVES)
    specialized = [query for wave in CIDER_SPECIALIZED_QUERY_WAVES for query in wave.values()]
    assert len(specialized) == len(set(specialized))


def test_relevance_accepts_apple_materials_for_cider_design() -> None:
    assessment = assess_cider_relevance(
        BibliographicRecord(
            source="OpenAlex",
            source_id="W-material",
            title="Polyphenol extraction and procyanidins in apple pomace",
            abstract=(
                "Apple pomace processing recovered phenolic compounds and tannins from cider "
                "raw material."
            ),
        ),
        "polyphenols",
    )

    assert assessment.status == "accepted"
    assert assessment.score >= 0.7


def test_bulk_harvest_rejects_unknown_source(settings) -> None:
    active = _active_settings(settings)
    database = Database(active.paths.database_path)
    database.initialize()

    with pytest.raises(ValueError, match="known bibliographic providers"):
        CiderBulkHarvester(active, database).run(
            target_new_accepted_abstracts=1,
            sources=("unknown",),
        )


def test_material_query_waves_cover_cider_inputs_and_coproducts() -> None:
    assert len(CIDER_MATERIAL_QUERY_WAVES) == 10
    assert all(set(wave) == set(CIDER_PILOT_THEMES) for wave in CIDER_MATERIAL_QUERY_WAVES)
    queries = [query for wave in CIDER_MATERIAL_QUERY_WAVES for query in wave.values()]
    assert len(queries) == len(set(queries))
    assert any("apple pomace" in query for query in queries)


def test_microbiology_query_waves_cover_fermentation_and_contamination() -> None:
    assert len(CIDER_MICROBIOLOGY_QUERY_WAVES) == 10
    assert all(set(wave) == set(CIDER_PILOT_THEMES) for wave in CIDER_MICROBIOLOGY_QUERY_WAVES)
    queries = [query for wave in CIDER_MICROBIOLOGY_QUERY_WAVES for query in wave.values()]
    assert len(queries) == len(set(queries))
    assert any("non-Saccharomyces" in query for query in queries)
    assert any("Alicyclobacillus" in query for query in queries)
    assert any("Penicillium expansum" in query for query in queries)
    assert any("Escherichia coli" in query for query in queries)
