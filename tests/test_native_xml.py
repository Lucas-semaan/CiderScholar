from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from app.database.sqlite import Database
from app.ingestion.chunker import ScientificChunker
from app.ingestion.native_pipeline import (
    NativeArticleMetadata,
    NativeAssetInput,
    NativeTextIngestionService,
)
from app.ingestion.native_xml import (
    JatsXmlExtractor,
    NativeXmlExtractionError,
    TeiXmlExtractor,
)
from app.ingestion.pdf_extractor import ExtractedDocument, StructuralTextBlock


def test_jats_parser_preserves_structural_locators_without_inventing_pages(tmp_path: Path) -> None:
    path = tmp_path / "article.xml"
    path.write_text(
        """<article xml:lang=\"en\">
        <front><article-meta><title-group><article-title>Native JATS</article-title></title-group>
        <abstract><p xml:id=\"abs-1\">An abstract.</p></abstract></article-meta></front>
        <body><sec xml:id=\"methods\"><title>Methods</title><p xml:id=\"m-1\">Method text.</p>
        <table-wrap xml:id=\"table-1\"><caption><p>Table caption.</p></caption></table-wrap>
        <sec><title>Sampling</title><p>Nested text.</p></sec></sec></body></article>""",
        encoding="utf-8",
    )

    document = JatsXmlExtractor().extract(path)

    assert document.source_format == "jats_xml"
    assert document.page_count == 0
    assert document.pages == []
    assert document.requires_ocr is False
    assert document.metadata["title"] == "Native JATS"
    assert [
        (block.kind, block.section_path, block.xml_id) for block in document.structural_blocks
    ] == [
        ("abstract", "Abstract", "abs-1"),
        ("paragraph", "Methods", "m-1"),
        ("table", "Methods", "table-1"),
        ("paragraph", "Methods / Sampling", None),
    ]
    assert document.outline_nodes[1].parent_node_id is None
    assert document.outline_nodes[-1].parent_node_id == document.outline_nodes[1].node_id


def test_tei_parser_extracts_sections_figures_and_tables(tmp_path: Path) -> None:
    path = tmp_path / "article.tei.xml"
    path.write_text(
        """<TEI xmlns=\"http://www.tei-c.org/ns/1.0\"><teiHeader><fileDesc>
        <titleStmt><title>Native TEI</title></titleStmt></fileDesc></teiHeader><text>
        <body><div xml:id=\"results\"><head>Results</head><p xml:id=\"r-1\">Finding.</p>
        <figure xml:id=\"fig-1\"><figDesc>Figure caption.</figDesc></figure>
        <table xml:id=\"tbl-1\"><row><cell>Value</cell></row></table></div></body></text></TEI>""",
        encoding="utf-8",
    )

    document = TeiXmlExtractor().extract(path)

    assert document.metadata["title"] == "Native TEI"
    assert [(block.kind, block.xml_id) for block in document.structural_blocks] == [
        ("paragraph", "r-1"),
        ("figure", "fig-1"),
        ("table", "tbl-1"),
    ]
    assert all(block.paragraph_number >= 1 for block in document.structural_blocks)


@pytest.mark.parametrize(
    "payload",
    ["<!DOCTYPE article><article><p>x</p></article>", "<!ENTITY x 'y'><article><p>x</p></article>"],
)
def test_native_xml_rejects_dtd_and_entity_declarations(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "unsafe.xml"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(NativeXmlExtractionError, match="forbidden"):
        JatsXmlExtractor().extract(path)


def test_extracted_document_rejects_duplicate_structural_source_blocks() -> None:
    block = StructuralTextBlock(
        block_id=StructuralTextBlock.make_block_id(
            kind="paragraph",
            section_path="Methods",
            paragraph_number=1,
            text="Source text.",
            xml_id="p1",
        ),
        kind="paragraph",
        section_path="Methods",
        paragraph_number=1,
        text="Source text.",
        xml_id="p1",
    )

    with pytest.raises(ValueError, match="cannot be duplicated"):
        ExtractedDocument(
            pdf_path="article.xml",
            page_count=0,
            pages=[],
            metadata={},
            text_character_count=0,
            text_page_count=0,
            requires_ocr=False,
            source_format="jats_xml",
            structural_blocks=[block, block],
        )


def test_native_ingestion_admits_jats_chunks_with_structural_locators(
    settings, tmp_path: Path
) -> None:
    path = tmp_path / "native.xml"
    path.write_text(
        """<article><front><article-meta><title-group><article-title>Native title</article-title>
        </title-group></article-meta></front><body><sec><title>Results</title>
        <p xml:id="result-1">Native fermentation result.</p></sec></body></article>""",
        encoding="utf-8",
    )
    database = Database(settings.paths.database_path)
    database.initialize()
    service = NativeTextIngestionService(
        database,
        ScientificChunker(target_tokens=20, max_tokens=40, overlap_tokens=0),
    )
    metadata = NativeArticleMetadata(
        doi="10.1000/native-jats",
        title="Validated bibliographic title",
        authors=["A. Expert"],
        source="europe_pmc",
    )
    asset = NativeAssetInput(
        path=path,
        format="jats_xml",
        sha256=sha256(path.read_bytes()).hexdigest(),
        media_type="application/xml",
        byte_count=path.stat().st_size,
        provider="europe_pmc",
    )

    report = service.ingest(metadata=metadata, asset=asset)
    repeated = service.ingest(metadata=metadata, asset=asset)

    assert report.reused_existing_asset is False
    assert report.chunk_count == 1
    assert repeated.reused_existing_asset is True
    assert repeated.chunk_count == 0
    article = database.article_by_doi("10.1000/native-jats")
    assert article is not None
    assert article["title"] == "Validated bibliographic title"
    assert article["pdf_path"] == ""
    chunk = database.chunks_for_article(report.article_id, limit=1)[0]
    assert chunk["page_start"] is None
    locator = database.chunk_locator(int(chunk["id"]))
    assert locator is not None
    assert locator["locator_kind"] == "structural"
    assert locator["section_path"] == "Results"
    assert locator["xml_id_start"] == "result-1"
    outline = database.document_outline(report.article_id)
    assert [(node["title"], node["source_locator"]) for node in outline] == [
        ("Results", "§ Results")
    ]
    inspection = database.article_inspection(report.article_id, limit=10, offset=0)
    assert inspection is not None
    assert inspection["chunks"][0]["outline_node_id"] == outline[0]["id"]
