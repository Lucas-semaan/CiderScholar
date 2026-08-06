from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.ingestion.pdf_extractor import (
    ExtractedDocument,
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
