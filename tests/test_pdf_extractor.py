from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ingestion.pdf_extractor import (
    DocumentOutlineNode,
    ExtractedDocument,
    ExtractionWarning,
    ParserIdentity,
    PyMuPdfExtractor,
    ScientificDocumentElement,
    sorted_page_text,
)


def test_sorted_page_text_uses_sorted_blocks_without_line_sort() -> None:
    class FakePage:
        def get_text(self, kind: str, *, sort: bool):
            assert kind == "blocks"
            assert sort is True
            return [
                (0, 0, 10, 10, "First block\n"),
                (0, 20, 10, 30, "Second block\x00"),
            ]

    assert sorted_page_text(FakePage()) == "First block\n\nSecond block"


def test_extracts_text_with_one_based_page_numbers(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "two-pages.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "First page contains enough scientific text for extraction.")
    page = document.new_page()
    page.insert_text((72, 72), "Second page preserves its own traceable page number.")
    document.save(pdf_path)
    document.close()

    extracted = PyMuPdfExtractor(min_page_text_characters=10, min_text_page_ratio=0.5).extract(
        pdf_path
    )

    assert extracted.page_count == 2
    assert [page.page_number for page in extracted.pages] == [1, 2]
    assert "First page" in extracted.pages[0].text
    assert "Second page" in extracted.pages[1].text
    assert extracted.requires_ocr is False


def test_empty_pdf_is_flagged_for_ocr(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "scanned.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()

    extracted = PyMuPdfExtractor().extract(pdf_path)
    assert extracted.requires_ocr is True


def test_extracts_table_cells_figure_caption_page_and_text_relation(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    image_module = pytest.importorskip("PIL.Image")
    pdf_path = tmp_path / "scientific-elements.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((50, 50), "Ce paragraphe décrit les résultats du tableau et de la figure.")
    table_rect = fitz.Rect(50, 100, 350, 220)
    page.draw_rect(table_rect)
    page.draw_line((200, 100), (200, 220))
    page.draw_line((50, 160), (350, 160))
    page.insert_text((70, 135), "Traitement")
    page.insert_text((220, 135), "Valeur")
    page.insert_text((70, 195), "Témoin")
    page.insert_text((220, 195), "4.2")
    page.insert_text((50, 245), "Tableau 1. Valeurs mesurées.")
    image = image_module.new("RGB", (80, 60), "navy")
    stream = BytesIO()
    image.save(stream, format="PNG")
    page.insert_image(fitz.Rect(380, 100, 540, 220), stream=stream.getvalue())
    page.insert_text((380, 245), "Figure 1. Profil observé.")
    document.save(pdf_path)
    document.close()

    extracted = PyMuPdfExtractor(
        min_page_text_characters=10,
        min_text_page_ratio=0.5,
    ).extract(pdf_path)

    tables = [item for item in extracted.elements if item.kind == "table"]
    figures = [item for item in extracted.elements if item.kind == "figure"]
    assert tables
    assert figures
    assert tables[0].page_number == 1
    assert any(cell.text == "Traitement" for cell in tables[0].cells)
    assert tables[0].original_caption == "Tableau 1. Valeurs mesurées."
    assert tables[0].synthetic_caption is None
    assert tables[0].text_relations[0].source_excerpt.startswith("Ce paragraphe")
    assert figures[0].original_caption == "Figure 1. Profil observé."
    assert figures[0].synthetic_caption is None
    restored = ExtractedDocument.from_dict(extracted.to_dict())
    assert restored.elements == extracted.elements
    assert isinstance(restored.elements[0], ScientificDocumentElement)


def test_extracted_document_round_trips_versioned_contract_and_legacy_cache() -> None:
    identity = ParserIdentity(
        parser_id="test_parser",
        parser_version="2.4.1",
        contract_version="1.0.0",
        config_sha256="a" * 64,
    )
    root = _outline_node(
        parent_node_id=None,
        level=1,
        kind="section",
        title="Results",
        ordinal=0,
        source_locator="page:2",
    )
    child = _outline_node(
        parent_node_id=root.node_id,
        level=2,
        kind="heading",
        title="Acidity",
        ordinal=1,
        source_locator="page:2:line:12",
    )
    document = ExtractedDocument(
        pdf_path="example.pdf",
        page_count=2,
        pages=[],
        metadata={},
        text_character_count=0,
        text_page_count=0,
        requires_ocr=True,
        source_format="pdf",
        parser_identity=identity,
        outline_nodes=[root, child],
        warnings=[ExtractionWarning(code="table_review", severity="review", page_number=2)],
    )

    restored = ExtractedDocument.from_dict(document.to_dict())
    assert restored == document
    assert restored.parser_identity == identity
    assert restored.outline_nodes == [root, child]
    assert restored.warnings[0].code == "table_review"

    legacy = document.to_dict()
    for field_name in ("source_format", "parser_identity", "outline_nodes", "warnings"):
        legacy.pop(field_name)
    legacy_restored = ExtractedDocument.from_dict(legacy)
    assert legacy_restored.source_format == "pdf"
    assert legacy_restored.parser_identity is None
    assert legacy_restored.outline_nodes == []
    assert legacy_restored.warnings == []


def test_contract_models_forbid_unknown_fields_and_incomplete_model_identity() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ParserIdentity.model_validate(
            {
                "parser_id": "test_parser",
                "parser_version": "1.0",
                "contract_version": "1.0.0",
                "config_sha256": "a" * 64,
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError, match="provided together"):
        ParserIdentity(
            parser_id="test_parser",
            parser_version="1.0",
            contract_version="1.0.0",
            config_sha256="a" * 64,
            model_name="layout-model",
        )


@pytest.mark.parametrize(
    "source_format",
    [
        "pdf",
        "jats_xml",
        "tei_xml",
        "structured_xml",
        "cleaned_text",
        "plain_text",
    ],
)
def test_extracted_document_accepts_only_canonical_source_formats(source_format: str) -> None:
    document = _empty_document(source_format=source_format)
    assert document.source_format == source_format

    with pytest.raises(ValueError, match="unsupported"):
        _empty_document(source_format="jats")


def test_contract_rejects_blank_strings_and_warning_ids_unsafe_for_adapters() -> None:
    identity_fields = {
        "parser_id": "test_parser",
        "parser_version": "1.0",
        "contract_version": "1.0.0",
        "config_sha256": "a" * 64,
    }
    with pytest.raises(ValidationError, match="parser version cannot be blank"):
        ParserIdentity(**{**identity_fields, "parser_version": "  "})
    with pytest.raises(ValidationError, match="model name cannot be blank"):
        ParserIdentity(**identity_fields, model_name="\t", model_sha256="b" * 64)
    with pytest.raises(ValidationError, match="source fields cannot be blank"):
        _outline_node(
            parent_node_id=None,
            level=1,
            kind="section",
            title=" ",
            ordinal=0,
            source_locator="page:1",
        )
    with pytest.raises(ValidationError, match="source fields cannot be blank"):
        _outline_node(
            parent_node_id=None,
            level=1,
            kind="section",
            title="Methods",
            ordinal=0,
            source_locator="\n",
        )

    assert (
        ExtractionWarning(
            code="jats_warning",
            severity="review",
            element_id="jats:sec.1-item_2",
        ).element_id
        == "jats:sec.1-item_2"
    )
    with pytest.raises(ValidationError):
        ExtractionWarning(code="jats_warning", severity="review", element_id="jats section")
    with pytest.raises(ValidationError):
        ExtractionWarning(code="jats_warning", severity="review", element_id="jats\nsection")


@pytest.mark.parametrize("invalid_outline", ["duplicate", "orphan", "cycle"])
def test_outline_rejects_duplicate_orphan_and_cyclic_nodes(invalid_outline: str) -> None:
    root = _outline_node(
        parent_node_id=None,
        level=1,
        kind="section",
        title="Methods",
        ordinal=0,
        source_locator="page:1",
    )
    if invalid_outline == "duplicate":
        nodes = [root, root]
        message = "duplicated"
    elif invalid_outline == "orphan":
        nodes = [
            DocumentOutlineNode.model_construct(
                node_id="outline-" + "b" * 24,
                parent_node_id="outline-" + "c" * 24,
                level=2,
                kind="heading",
                title="Missing parent",
                ordinal=1,
                source_locator="page:1:line:4",
            )
        ]
        message = "parent is missing"
    else:
        nodes = [
            DocumentOutlineNode.model_construct(
                node_id="outline-" + "d" * 24,
                parent_node_id="outline-" + "d" * 24,
                level=1,
                kind="section",
                title="Cycle",
                ordinal=0,
                source_locator="page:1",
            )
        ]
        message = "cycle"

    with pytest.raises(ValueError, match=message):
        ExtractedDocument(
            pdf_path="example.pdf",
            page_count=1,
            pages=[],
            metadata={},
            text_character_count=0,
            text_page_count=0,
            requires_ocr=True,
            outline_nodes=nodes,
        )


@pytest.mark.parametrize(
    "invalid_outline", ["ordinal", "parent_order", "root_level", "child_level"]
)
def test_outline_rejects_invalid_order_and_levels(invalid_outline: str) -> None:
    root = _outline_node(
        parent_node_id=None,
        level=1,
        kind="section",
        title="Results",
        ordinal=1 if invalid_outline == "parent_order" else 0,
        source_locator="page:2",
    )
    child = _outline_node(
        parent_node_id=root.node_id,
        level=2,
        kind="heading",
        title="Acidity",
        ordinal=0 if invalid_outline == "parent_order" else 1,
        source_locator="page:2:line:12",
    )
    if invalid_outline == "ordinal":
        nodes = [root, child.model_copy(update={"ordinal": root.ordinal})]
        message = "ordinals"
    elif invalid_outline == "parent_order":
        nodes = [child, root]
        message = "parent must precede"
    elif invalid_outline == "root_level":
        nodes = [root.model_copy(update={"level": 2}), child]
        message = "root node must have level one"
    else:
        nodes = [root, child.model_copy(update={"level": 1})]
        message = "child level"

    with pytest.raises(ValueError, match=message):
        _empty_document(outline_nodes=nodes)


def test_pymupdf_emits_builtin_identity_and_empty_optional_contract_fields(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "identity.pdf"
    document = fitz.open()
    document.new_page().insert_text((72, 72), "Traceable parser identity.")
    document.save(pdf_path)
    document.close()

    extracted = PyMuPdfExtractor().extract(pdf_path)

    assert extracted.source_format == "pdf"
    assert extracted.parser_identity is not None
    assert extracted.parser_identity.parser_id == "pymupdf"
    assert extracted.parser_identity.parser_version == str(fitz.VersionBind)
    assert extracted.parser_identity.contract_version == "1.0.0"
    assert len(extracted.parser_identity.config_sha256) == 64
    assert extracted.outline_nodes == []
    assert extracted.warnings == []


def _outline_node(
    *,
    parent_node_id: str | None,
    level: int,
    kind: str,
    title: str,
    ordinal: int,
    source_locator: str,
) -> DocumentOutlineNode:
    return DocumentOutlineNode(
        node_id=DocumentOutlineNode.make_node_id(
            parent_node_id=parent_node_id,
            level=level,
            kind=kind,
            title=title,
            ordinal=ordinal,
            source_locator=source_locator,
        ),
        parent_node_id=parent_node_id,
        level=level,
        kind=kind,
        title=title,
        ordinal=ordinal,
        source_locator=source_locator,
    )


def _empty_document(
    *,
    source_format: str = "pdf",
    outline_nodes: list[DocumentOutlineNode] | None = None,
) -> ExtractedDocument:
    return ExtractedDocument(
        pdf_path="example.pdf",
        page_count=1,
        pages=[],
        metadata={},
        text_character_count=0,
        text_page_count=0,
        requires_ocr=True,
        source_format=source_format,
        outline_nodes=outline_nodes or [],
    )
