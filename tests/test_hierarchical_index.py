from __future__ import annotations

from contextlib import closing

from app.database.sqlite import Database
from app.retrieval.hierarchical_index import SqliteHierarchicalIndex


def test_hierarchical_index_keeps_anchor_neighbors_and_original_sqlite_text(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    chunks = [
        ("Introduction", "Background unrelated to the measured outcome."),
        ("Results", "First result before the target."),
        ("Results", "Target result on apple pressing yield."),
        ("Results", "Condition attached to the target result."),
        ("Discussion", "Limit of the pressing experiment."),
        ("Materials and Methods", "Detailed analytical protocol."),
        ("Other", "Remote appendix material."),
    ]
    database.save_article_and_chunks(
        {
            "id": "article-hierarchy",
            "sha256": "d" * 64,
            "doi": None,
            "title": "Hierarchical article",
            "abstract": chunks[0][1],
            "authors": [],
            "journal": None,
            "publication_year": 2025,
            "language": "en",
            "pdf_path": "data/pdf/hierarchy.pdf",
            "validation_status": "indexed",
            "source": "local",
        },
        [
            {
                "section": section,
                "page_start": index + 1,
                "page_end": index + 1,
                "chunk_index": index,
                "text": text,
                "token_count": len(text.split()),
                "embedding_status": "indexed",
            }
            for index, (section, text) in enumerate(chunks)
        ],
    )
    with closing(database.connect()) as connection:
        anchor_id = int(
            connection.execute(
                "SELECT id FROM chunks WHERE article_id = ? AND chunk_index = 2",
                ("article-hierarchy",),
            ).fetchone()["id"]
        )

    rows, trace = SqliteHierarchicalIndex(database).navigate(
        article_id="article-hierarchy",
        anchor_chunk_ids=[anchor_id],
        candidate_limit=5,
        neighborhood_radius=1,
        include_methods=False,
    )

    assert int(rows[0]["id"]) == anchor_id
    assert [int(row["chunk_index"]) for row in rows[:3]] == [2, 1, 3]
    assert str(rows[0]["text"]) == chunks[2][1]
    assert "Materials and Methods" not in trace.selected_section_names
    assert trace.anchor_chunk_ids == [anchor_id]
