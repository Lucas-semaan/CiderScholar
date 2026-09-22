from __future__ import annotations

import pytest

from app.database.sqlite import Database
from app.ingestion.pdf_extractor import DocumentOutlineNode


def _node(*, parent_node_id: str | None, level: int, title: str, ordinal: int) -> dict[str, object]:
    locator = f"§ {title}"
    return DocumentOutlineNode(
        node_id=DocumentOutlineNode.make_node_id(
            parent_node_id=parent_node_id,
            level=level,
            kind="section",
            title=title,
            ordinal=ordinal,
            source_locator=locator,
        ),
        parent_node_id=parent_node_id,
        level=level,
        kind="section",
        title=title,
        ordinal=ordinal,
        source_locator=locator,
    ).model_dump(mode="python")


def test_persisted_outline_is_hashed_and_links_only_own_article_chunks(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-outline",
            "sha256": "a" * 64,
            "title": "Outline article",
            "pdf_path": "data/common/pdf/outline.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Methods",
                "subsection": None,
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Method text.",
                "token_count": 3,
            }
        ],
    )
    asset_id = database.save_article_source_asset(
        article_id="article-outline",
        kind="pdf",
        file_path="data/common/pdf/outline.pdf",
        sha256="a" * 64,
        media_type="application/pdf",
        byte_count=10,
        is_primary=True,
    )
    root = _node(parent_node_id=None, level=1, title="Methods", ordinal=0)
    child = _node(parent_node_id=str(root["node_id"]), level=2, title="Sampling", ordinal=1)

    durable_ids = database.save_document_outline(
        article_id="article-outline", asset_id=asset_id, nodes=[root, child]
    )
    chunk = database.chunks_for_article("article-outline", limit=1)[0]
    database.save_chunk_outline_links(
        article_id="article-outline",
        local_to_durable_node_ids=durable_ids,
        chunk_to_local_node_ids={int(chunk["id"]): str(child["node_id"])},
    )

    outline = database.document_outline("article-outline")
    assert [row["local_node_id"] for row in outline] == [root["node_id"], child["node_id"]]
    assert all(len(str(row["structure_sha256"])) == 64 for row in outline)
    with database.connect() as connection:
        link = connection.execute("SELECT outline_node_id FROM chunk_outline_nodes").fetchone()
    assert link["outline_node_id"] == durable_ids[str(child["node_id"])]


def test_outline_rejects_a_parent_not_present_in_the_same_asset(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-invalid-outline",
            "sha256": "b" * 64,
            "title": "Invalid outline",
            "pdf_path": "data/common/pdf/invalid.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [],
    )
    asset_id = database.save_article_source_asset(
        article_id="article-invalid-outline",
        kind="pdf",
        file_path="data/common/pdf/invalid.pdf",
        sha256="b" * 64,
        media_type="application/pdf",
        byte_count=10,
    )
    node = _node(
        parent_node_id="outline-aaaaaaaaaaaaaaaaaaaaaaaa", level=2, title="Child", ordinal=0
    )

    with pytest.raises(ValueError, match="parent is missing"):
        database.save_document_outline(
            article_id="article-invalid-outline", asset_id=asset_id, nodes=[node]
        )
