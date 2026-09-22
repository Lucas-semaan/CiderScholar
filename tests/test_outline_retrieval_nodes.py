from app.database.sqlite import Database
from app.ingestion.pdf_extractor import DocumentOutlineNode


def test_outline_navigation_nodes_are_derived_and_never_citable(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {"id": "a", "sha256": "a" * 64, "title": "A", "pdf_path": "a.pdf"},
        [
            {"page_start": 1, "page_end": 1, "chunk_index": 0, "text": "Source.", "token_count": 1},
            {
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 1,
                "text": "Sibling.",
                "token_count": 1,
            },
        ],
    )
    asset_id = database.save_article_source_asset(
        article_id="a",
        kind="pdf",
        file_path="a.pdf",
        sha256="a" * 64,
        media_type="application/pdf",
        byte_count=1,
    )
    node = DocumentOutlineNode(
        node_id=DocumentOutlineNode.make_node_id(
            parent_node_id=None,
            level=1,
            kind="section",
            title="Methods",
            ordinal=0,
            source_locator="§ Methods",
        ),
        parent_node_id=None,
        level=1,
        kind="section",
        title="Methods",
        ordinal=0,
        source_locator="§ Methods",
    )
    node_ids = database.save_document_outline(
        article_id="a", asset_id=asset_id, nodes=[node.model_dump()]
    )
    chunk_ids = [int(chunk["id"]) for chunk in database.chunks_for_article("a", limit=2)]
    database.save_chunk_outline_links(
        article_id="a",
        local_to_durable_node_ids=node_ids,
        chunk_to_local_node_ids={chunk_id: node.node_id for chunk_id in chunk_ids},
    )
    assert database.rebuild_outline_retrieval_nodes("a") == 1
    with database.connect() as connection:
        row = connection.execute("SELECT text, citable FROM outline_retrieval_nodes").fetchone()
    assert row["text"] == "Methods\n§ Methods"
    assert row["citable"] == 0
    with database.connect() as connection:
        retrieval_id = connection.execute("SELECT id FROM outline_retrieval_nodes").fetchone()[0]
    assert [row["text"] for row in database.chunks_for_outline_retrieval_node(retrieval_id)] == [
        "Source.",
        "Sibling.",
    ]
    assert database.outline_expanded_chunk_ids(
        [chunk_ids[0]], candidate_limit=20, passages_per_article=6
    ) == [chunk_ids[1]]
