from __future__ import annotations

from contextlib import closing

import pytest

from app.database.sqlite import Database
from app.updates.harvest import bibliographic_content_hash
from app.updates.scopus_text import (
    ScopusTextExportError,
    apply_scopus_source_title_plan,
    parse_scopus_citation,
    parse_scopus_text,
    plan_scopus_source_title_reconciliation,
)


def _record(eid: str, citation: str, *, title: str = "Cider source-title study") -> str:
    return f"""
Martin A., Dupont B.
AUTHOR FULL NAMES: Martin, Alice (123); Dupont, Bob (456)
123; 456
{title}
{citation}
DOI: 10.1000/CIDER
https://www.scopus.com/pages/publications/{eid}?origin=resultslist

ABSTRACT: A local abstract about cider fermentation.
DOCUMENT TYPE: Article
PUBLICATION STAGE: Final
SOURCE: Scopus
"""


def _insert_bibliographic_record(
    database: Database,
    *,
    record_id: str,
    journal: str | None,
    source_ids: list[str],
) -> str:
    values = {
        "doi": "10.1000/cider",
        "title": "Cider source-title study",
        "abstract": "A local abstract about cider fermentation.",
        "authors": '["Martin, Alice", "Dupont, Bob"]',
        "journal": journal,
        "work_type": "Article",
        "publisher": "Existing Publisher",
        "publication_year": 2020,
        "citation_count": 3,
        "url": "https://www.scopus.com/pages/publications/1",
    }
    content_hash = bibliographic_content_hash(values)
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, journal,
                work_type, publisher, publication_year, citation_count, url,
                content_hash, embedding_status, relevance_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed', 'accepted')
            """,
            (
                record_id,
                f"doi:10.1000/{record_id}",
                values["doi"],
                values["title"],
                values["abstract"],
                values["authors"],
                values["journal"],
                values["work_type"],
                values["publisher"],
                values["publication_year"],
                values["citation_count"],
                values["url"],
                content_hash,
            ),
        )
        connection.executemany(
            """
            INSERT INTO bibliographic_record_sources (record_id, source, source_id)
            VALUES (?, 'scopus', ?)
            """,
            [(record_id, source_id) for source_id in source_ids],
        )
    return content_hash


@pytest.mark.parametrize(
    ("line", "source_title", "citation_count"),
    [
        (
            "(2025) Food, Culture and Society, 28 (3), pp. 711 - 732, Cited 1 times.",
            "Food, Culture and Society",
            1,
        ),
        (
            "(2018) Fruit Juices: Extraction, Composition, Quality and Analysis, "
            "pp. 823 - 833, Cited 5 times.",
            "Fruit Juices: Extraction, Composition, Quality and Analysis",
            5,
        ),
        (
            '(1995) "Palaeogeography, Palaeoclimatology, Palaeoecology", 113 (2-4), '
            "pp. 243 - 248, Cited 171 times.",
            "Palaeogeography, Palaeoclimatology, Palaeoecology",
            171,
        ),
        (
            "(2020) Foods, 10 (2), art. no. 412, pp. 1 - 16, Cited 25 times.",
            "Foods",
            25,
        ),
    ],
)
def test_scopus_citation_is_parsed_from_the_right(
    line: str,
    source_title: str,
    citation_count: int,
) -> None:
    citation = parse_scopus_citation(line)

    assert citation.source_title == source_title
    assert citation.citation_count == citation_count


def test_ambiguous_scopus_citation_fails_closed() -> None:
    with pytest.raises(ScopusTextExportError, match="ambiguous"):
        parse_scopus_citation("(2020) Food, Culture and Society")


def test_scopus_text_parser_keeps_source_title_separate_from_publisher() -> None:
    records = parse_scopus_text(
        "Scopus\nEXPORT DATE: 28 August 2026\n"
        + _record(
            "851",
            "(2020) Food, Culture and Society, 21 (3), pp. 1 - 8, Cited 3 times.",
        )
    )

    assert len(records) == 1
    assert records[0].citation.source_title == "Food, Culture and Society"
    assert records[0].authors == ("Martin, Alice", "Dupont, Bob")
    assert records[0].doi == "10.1000/cider"
    bibliographic = records[0].to_bibliographic_record()
    assert bibliographic.journal == "Food, Culture and Society"
    assert bibliographic.publisher is None


def test_reconciliation_persists_per_eid_title_and_repairs_only_legacy_truncation(
    settings,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    old_hash = _insert_bibliographic_record(
        database,
        record_id="record-1",
        journal="Food",
        source_ids=["851"],
    )
    records = parse_scopus_text(
        _record(
            "851",
            "(2020) Food, Culture and Society, 21 (3), pp. 1 - 8, Cited 3 times.",
        )
    )

    with database.transaction() as connection:
        plan = plan_scopus_source_title_reconciliation(connection, records)
        assert plan.summary()["scalar_journals_to_repair"] == 1
        assert plan.summary()["per_source_titles_to_persist"] == 1
        applied = apply_scopus_source_title_plan(connection, plan)

    assert applied == {
        "source_metadata_updated": 1,
        "archived_source_metadata_updated": 0,
        "journals_updated": 1,
    }
    with closing(database.connect()) as connection:
        row = connection.execute(
            """
            SELECT r.journal, r.publisher, r.content_hash, r.embedding_status, s.source_title
            FROM bibliographic_records AS r
            JOIN bibliographic_record_sources AS s ON s.record_id = r.id
            WHERE r.id = 'record-1'
            """
        ).fetchone()
        fts = connection.execute(
            "SELECT journal FROM bibliographic_records_fts WHERE record_id = 'record-1'"
        ).fetchone()
        second_plan = plan_scopus_source_title_reconciliation(connection, records)

    assert row["journal"] == "Food, Culture and Society"
    assert row["source_title"] == "Food, Culture and Society"
    assert row["publisher"] == "Existing Publisher"
    assert row["content_hash"] != old_hash
    assert row["embedding_status"] == "indexed"
    assert fts["journal"] == "Food, Culture and Society"
    assert second_plan.source_metadata_updates == ()
    assert second_plan.journal_updates == ()


def test_reconciliation_preserves_nontruncated_canonical_journal(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_bibliographic_record(
        database,
        record_id="record-1",
        journal="Food Additives & Contaminants Part A",
        source_ids=["851"],
    )
    records = parse_scopus_text(
        _record(
            "851",
            "(2020) Food Additives and Contaminants - Part A Chemistry, Analysis, Control, "
            "Exposure and Risk Assessment, 37 (4), pp. 607 - 621, Cited 34 times.",
        )
    )

    with database.transaction() as connection:
        plan = plan_scopus_source_title_reconciliation(connection, records)
        assert plan.journal_updates == ()
        apply_scopus_source_title_plan(connection, plan)

    with closing(database.connect()) as connection:
        row = connection.execute(
            """
            SELECT r.journal, s.source_title
            FROM bibliographic_records AS r
            JOIN bibliographic_record_sources AS s ON s.record_id = r.id
            """
        ).fetchone()
    assert row["journal"] == "Food Additives & Contaminants Part A"
    assert row["source_title"].endswith("Exposure and Risk Assessment")


def test_multiple_scopus_eids_keep_each_source_title_before_scalar_preference(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_bibliographic_record(
        database,
        record_id="record-1",
        journal="Starch in Food: Structure",
        source_ids=["851", "852"],
    )
    records = parse_scopus_text(
        _record(
            "851",
            "(2018) Starch in Food: Structure, Function and Applications: Second Edition, "
            "pp. 633 - 659, Cited 34 times.",
        )
        + _record(
            "852",
            "(2017) Starch in Food: Structure, Function and Applications, "
            "pp. 633 - 659, Cited 34 times.",
        )
    )

    with database.transaction() as connection:
        plan = plan_scopus_source_title_reconciliation(connection, records)
        assert plan.record_source_title_conflicts == 1
        assert plan.journal_updates[0].journal.endswith("Second Edition")
        apply_scopus_source_title_plan(connection, plan)

    with closing(database.connect()) as connection:
        titles = {
            row[0]
            for row in connection.execute(
                "SELECT source_title FROM bibliographic_record_sources ORDER BY source_id"
            )
        }
    assert titles == {
        "Starch in Food: Structure, Function and Applications",
        "Starch in Food: Structure, Function and Applications: Second Edition",
    }


def test_reconciliation_persists_source_title_for_archived_doi(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO rejected_bibliographic_archive (
                original_record_id, canonical_key, doi, title, sources, harvest_run_ids
            ) VALUES (
                'archived-1', 'doi:10.1000/cider', '10.1000/cider',
                'Cider source-title study', '[]', '[]'
            )
            """
        )
    records = parse_scopus_text(
        _record(
            "ARCHIVED-EID",
            "(2020) Food, Culture and Society, 21 (3), pp. 1 - 8, Cited 3 times.",
        )
    )

    with database.transaction() as connection:
        plan = plan_scopus_source_title_reconciliation(connection, records)
        assert len(plan.archived_source_metadata_updates) == 1
        assert plan.unmatched_source_ids == ()
        apply_scopus_source_title_plan(connection, plan)

    with closing(database.connect()) as connection:
        row = connection.execute(
            """
            SELECT source_id, source_title
            FROM rejected_bibliographic_record_sources
            WHERE original_record_id = 'archived-1' AND source = 'scopus'
            """
        ).fetchone()
        second_plan = plan_scopus_source_title_reconciliation(connection, records)

    assert row["source_id"] == "ARCHIVED-EID"
    assert row["source_title"] == "Food, Culture and Society"
    assert second_plan.archived_source_metadata_updates == ()
