import pytest

from app.database.sqlite import Database
from app.models.table_evidence import table_evidence_from_element


def test_table_evidence_validates_numbers_against_source_cells() -> None:
    evidence = table_evidence_from_element(
        {
            "id": "article:table-p0001-001",
            "article_id": "article",
            "kind": "table",
            "page_number": 1,
            "cells": [
                {"row_index": 0, "column_index": 0, "text": "Concentration (%)"},
                {"row_index": 1, "column_index": 0, "text": "12,5 %"},
            ],
            "text_relations": [{"related_chunk_id": 8}],
        }
    )
    assert evidence.contains_numeric_claim("12.5")
    assert evidence.contains_numeric_claim("12,5 %")
    assert not evidence.contains_numeric_claim("13")
    assert evidence.related_chunk_ids == (8,)


def test_table_evidence_rejects_figures_and_duplicate_cells() -> None:
    with pytest.raises(ValueError, match="source table"):
        table_evidence_from_element({"kind": "figure"})
    with pytest.raises(ValueError, match="duplicated"):
        table_evidence_from_element(
            {
                "id": "table",
                "article_id": "article",
                "kind": "table",
                "page_number": 1,
                "cells": [
                    {"row_index": 0, "column_index": 0, "text": "A"},
                    {"row_index": 0, "column_index": 0, "text": "B"},
                ],
            }
        )


def test_database_projects_only_persisted_source_tables(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {"id": "article", "sha256": "a" * 64, "title": "A", "pdf_path": "a.pdf"},
        [
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Near table",
                "token_count": 2,
            }
        ],
        [
            {
                "element_id": "table-p0001-001",
                "kind": "table",
                "page_number": 1,
                "bbox": [0, 0, 1, 1],
                "source_kind": "pdf_embedded",
                "cells": [{"row_index": 0, "column_index": 0, "text": "42"}],
                "text_relations": [
                    {
                        "relation": "nearest_page_text",
                        "page_number": 1,
                        "source_excerpt": "Near table",
                    }
                ],
            },
            {
                "element_id": "figure-p0001-002",
                "kind": "figure",
                "page_number": 1,
                "bbox": [1, 1, 2, 2],
                "source_kind": "pdf_embedded",
            },
        ],
    )
    evidence = database.table_evidence("article")
    assert len(evidence) == 1
    assert evidence[0].contains_numeric_claim("42")
