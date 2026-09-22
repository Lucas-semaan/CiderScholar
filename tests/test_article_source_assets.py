"""Migration coverage for generic, source-authoritative article assets."""

from __future__ import annotations

from contextlib import closing

from app.database.sqlite import Database


def test_source_asset_migration_backfills_a_legacy_pdf_without_removing_pdf_path(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "legacy-article",
            "sha256": "a" * 64,
            "title": "Legacy PDF article",
            "pdf_path": "data/common/pdf/legacy.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [],
    )
    with closing(database.connect()) as connection, connection:
        connection.execute("DROP TABLE article_source_assets")
        connection.execute("DELETE FROM schema_version WHERE version >= ?", (39,))

    database.initialize()

    with closing(database.connect()) as connection:
        asset = connection.execute(
            """
            SELECT article_id, kind, file_path, sha256, media_type, state, is_primary
            FROM article_source_assets
            """
        ).fetchone()
        article = connection.execute(
            "SELECT pdf_path FROM articles WHERE id = 'legacy-article'"
        ).fetchone()

    assert tuple(asset) == (
        "legacy-article",
        "pdf",
        "data/common/pdf/legacy.pdf",
        "a" * 64,
        "application/pdf",
        "admitted",
        1,
    )
    assert article["pdf_path"] == "data/common/pdf/legacy.pdf"


def test_source_assets_keep_multiple_verified_formats_and_switch_primary_atomically(
    settings,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-1",
            "sha256": "b" * 64,
            "title": "Source asset article",
            "pdf_path": "data/common/pdf/source.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [],
    )

    pdf_asset_id = database.save_article_source_asset(
        article_id="article-1",
        kind="pdf",
        file_path="data/common/pdf/source.pdf",
        sha256="b" * 64,
        media_type="application/pdf",
        byte_count=42,
        provider="local",
        is_primary=True,
    )
    xml_asset_id = database.save_article_source_asset(
        article_id="article-1",
        kind="jats_xml",
        file_path="data/common/full-text/source.jats.xml",
        sha256="c" * 64,
        media_type="application/xml",
        byte_count=84,
        provider="europe_pmc",
        source_url="https://example.test/source.xml",
        license="CC BY",
        is_primary=True,
    )

    assets = database.article_source_assets("article-1")

    assert [asset["id"] for asset in assets] == [xml_asset_id, pdf_asset_id]
    assert [asset["is_primary"] for asset in assets] == [1, 0]
    assert {asset["kind"] for asset in assets} == {"pdf", "jats_xml"}


def test_structural_locator_replaces_page_fields_without_inventing_a_page(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "article-structural",
            "sha256": "d" * 64,
            "title": "Structured article",
            "pdf_path": "data/common/pdf/structured.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "section": "Results",
                "subsection": None,
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "text": "Native structured result.",
                "token_count": 4,
            }
        ],
    )
    asset_id = database.save_article_source_asset(
        article_id="article-structural",
        kind="jats_xml",
        file_path="data/common/full-text/structured.xml",
        sha256="e" * 64,
        media_type="application/xml",
        byte_count=99,
    )
    chunk = database.chunks_for_article("article-structural", limit=1)[0]

    database.save_structural_chunk_locator(
        chunk_id=int(chunk["id"]),
        asset_id=asset_id,
        section_path="Results/Fermentation",
        paragraph_start=3,
        paragraph_end=3,
        xml_id_start="p-4",
        xml_id_end="p-4",
        span_text="Native structured result.",
    )

    locator = database.chunk_locator(int(chunk["id"]))
    assert locator is not None
    assert locator["locator_kind"] == "structural"
    assert locator["page_start"] is None
    assert locator["page_end"] is None
    assert locator["section_path"] == "Results/Fermentation"


def test_native_chunk_can_be_persisted_without_fake_page_coordinates(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "native-only",
            "sha256": "f" * 64,
            "title": "Native XML article",
            "pdf_path": "",
            "validation_status": "validated",
            "source": "europe_pmc",
        },
        [
            {
                "section": "Results",
                "page_start": None,
                "page_end": None,
                "chunk_index": 0,
                "text": "A source-native structural result.",
                "token_count": 4,
            }
        ],
    )
    asset_id = database.save_article_source_asset(
        article_id="native-only",
        kind="jats_xml",
        file_path="data/common/full-text/native-only.xml",
        sha256="e" * 64,
        media_type="application/xml",
        byte_count=100,
        provider="europe_pmc",
    )
    chunk = database.chunks_for_article("native-only", limit=1)[0]
    database.save_structural_chunk_locator(
        chunk_id=int(chunk["id"]),
        asset_id=asset_id,
        section_path="Results",
        paragraph_start=1,
        paragraph_end=1,
        xml_id_start="result-1",
        xml_id_end="result-1",
        span_text="A source-native structural result.",
    )

    locator = database.chunk_locator(int(chunk["id"]))
    assert chunk["page_start"] is None
    assert chunk["page_end"] is None
    assert locator is not None
    assert locator["locator_kind"] == "structural"


def test_page_constraint_migration_preserves_chunk_ids_and_fts(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    database.save_article_and_chunks(
        {
            "id": "migration-article",
            "sha256": "1" * 64,
            "title": "Migration article",
            "pdf_path": "data/common/pdf/migration.pdf",
            "validation_status": "validated",
            "source": "local",
        },
        [
            {
                "page_start": 3,
                "page_end": 3,
                "chunk_index": 0,
                "text": "A durable migration retrieval token.",
                "token_count": 5,
            }
        ],
    )
    previous_chunk_id = int(database.chunks_for_article("migration-article", limit=1)[0]["id"])
    with closing(database.connect()) as connection, connection:
        connection.execute("DELETE FROM schema_version WHERE version = 44")

    database.initialize()

    chunk = database.chunks_for_article("migration-article", limit=1)[0]
    matches = database.lexical_search('"migration"', limit=10)
    assert int(chunk["id"]) == previous_chunk_id
    assert (chunk["page_start"], chunk["page_end"]) == (3, 3)
    assert [int(row["id"]) for row in matches] == [previous_chunk_id]


def test_native_admission_is_atomic_and_idempotent(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    article = {
        "id": "native-admission",
        "sha256": "2" * 64,
        "doi": "10.1000/native-admission",
        "title": "Native admission article",
        "authors": ["A. Expert"],
        "source": "europe_pmc",
    }
    asset = {
        "kind": "jats_xml",
        "file_path": "data/common/full-text/native-admission.xml",
        "sha256": "2" * 64,
        "media_type": "application/xml",
        "byte_count": 200,
        "provider": "europe_pmc",
        "source_url": "https://example.test/native-admission.xml",
        "license": "CC BY",
    }
    chunks = [
        {
            "section": "Results",
            "text": "A native structural result.",
            "token_count": 5,
            "section_path": "Results",
            "paragraph_start": 2,
            "paragraph_end": 2,
            "xml_id_start": "result-2",
            "xml_id_end": "result-2",
        }
    ]

    article_id, chunk_ids, reused = database.admit_native_asset_and_chunks(
        article=article,
        asset=asset,
        chunks=chunks,
    )
    duplicate_id, duplicate_chunks, duplicate_reused = database.admit_native_asset_and_chunks(
        article=article,
        asset=asset,
        chunks=chunks,
    )

    assert article_id == "native-admission"
    assert reused is False
    assert duplicate_id == article_id
    assert duplicate_chunks == []
    assert duplicate_reused is True
    persisted_chunk = database.chunks_for_article(article_id, limit=10)[0]
    assert int(persisted_chunk["id"]) == chunk_ids[0]
    assert persisted_chunk["page_start"] is None
    locator = database.chunk_locator(chunk_ids[0])
    assert locator is not None
    assert locator["locator_kind"] == "structural"
    assert locator["section_path"] == "Results"
