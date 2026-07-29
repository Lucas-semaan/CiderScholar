from __future__ import annotations

from pathlib import Path

from app.database.sqlite import Database
from app.ingestion.pdf_extractor import (
    ElementTextRelation,
    ExtractedDocument,
    OcrPageTrace,
    PageText,
    ScientificDocumentElement,
    TableCell,
)
from app.ingestion.pipeline import IngestionPipeline, PdfCatalogMetadata


class FakeExtractor:
    def __init__(
        self,
        *,
        requires_ocr: bool = False,
        doi: str | None = None,
        elements: list[ScientificDocumentElement] | None = None,
        ocr_pages: list[OcrPageTrace] | None = None,
    ) -> None:
        self.requires_ocr = requires_ocr
        self.doi = doi
        self.elements = elements or []
        self.ocr_pages = ocr_pages or []
        self.calls = 0

    def extract(self, pdf_path: Path) -> ExtractedDocument:
        self.calls += 1
        text = (
            ""
            if self.requires_ocr
            else (
                "Synthetic Study of Local Retrieval\n"
                f"DOI: {self.doi or ''}\n"
                "Abstract\nA local synthetic abstract without external data.\n"
                "Results\nThe PAGE_MARKER result is reproducible and entirely synthetic."
            )
        )
        return ExtractedDocument(
            pdf_path=str(pdf_path.resolve()),
            page_count=1,
            pages=[PageText(1, text)],
            metadata={"title": "Synthetic Study", "author": "Ada Test"},
            text_character_count=len(text),
            text_page_count=0 if self.requires_ocr else 1,
            requires_ocr=self.requires_ocr,
            elements=self.elements,
            ocr_pages=self.ocr_pages,
        )


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(b"synthetic test PDF bytes; extractor is replaced")
    return path


def test_pipeline_persists_chunks_and_detects_duplicate(settings, tmp_path: Path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    extractor = FakeExtractor()
    pipeline = IngestionPipeline(settings, database, extractor=extractor)
    pdf = _pdf(tmp_path)

    first = pipeline.ingest_file(pdf)
    second = pipeline.ingest_file(pdf)

    assert first.status == "chunks_ready"
    assert first.chunk_count >= 1
    assert second.status == "duplicate"
    assert second.article_id == first.article_id
    assert extractor.calls == 1
    assert database.lexical_search("PAGE_MARKER")[0]["article_id"] == first.article_id


def test_pipeline_reports_ocr_without_starting_ocr(settings, tmp_path: Path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    pipeline = IngestionPipeline(settings, database, extractor=FakeExtractor(requires_ocr=True))

    report = pipeline.ingest_file(_pdf(tmp_path))

    assert report.status == "ocr_required"
    assert report.article_id is None


def test_low_confidence_ocr_is_traced_but_never_chunked_as_evidence(
    settings,
    tmp_path: Path,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    trace = OcrPageTrace(
        page_number=1,
        language="fr-FR",
        confidence=0.21,
        embedded_text_original="",
        ocr_text="INVENTED_OCR_SENTINEL @@@",
        admitted=False,
        decision_reason="ocr_low_confidence",
    )
    pipeline = IngestionPipeline(
        settings,
        database,
        extractor=FakeExtractor(requires_ocr=True, ocr_pages=[trace]),
    )

    report = pipeline.ingest_file(_pdf(tmp_path))
    stored = database.ocr_page_traces(report.sha256)

    assert report.status == "ocr_required"
    assert report.ocr_uncertain_page_count == 1
    assert stored[0]["language"] == "fr-FR"
    assert stored[0]["confidence"] == 0.21
    assert stored[0]["ocr_text"] == "INVENTED_OCR_SENTINEL @@@"
    assert stored[0]["admitted"] == 0
    assert stored[0]["article_id"] is None
    assert database.lexical_search("INVENTED_OCR_SENTINEL") == []


def test_pipeline_can_refresh_a_cached_ocr_required_document(settings, tmp_path: Path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    pdf = _pdf(tmp_path)
    initial = IngestionPipeline(
        settings,
        database,
        extractor=FakeExtractor(requires_ocr=True),
    ).ingest_file(pdf)
    replacement = FakeExtractor()

    refreshed = IngestionPipeline(
        settings,
        database,
        extractor=replacement,
        refresh_ocr_cache=True,
    ).ingest_file(pdf)

    assert initial.status == "ocr_required"
    assert refreshed.status == "chunks_ready"
    assert replacement.calls == 1


def test_pipeline_persists_tables_without_merging_source_and_enrichment(
    settings,
    tmp_path: Path,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    relation_text = "The PAGE_MARKER result is reproducible and entirely synthetic."
    element = ScientificDocumentElement(
        element_id="table-p0001-001",
        kind="table",
        page_number=1,
        bbox=(10.0, 20.0, 300.0, 180.0),
        source_kind="pdf_embedded",
        original_caption="Table 1. Synthetic values.",
        synthetic_caption=None,
        cells=[
            TableCell(row_index=0, column_index=0, text="Treatment"),
            TableCell(row_index=0, column_index=1, text="Value"),
        ],
        text_relations=[ElementTextRelation(page_number=1, source_excerpt=relation_text)],
    )
    pipeline = IngestionPipeline(
        settings,
        database,
        extractor=FakeExtractor(elements=[element]),
    )

    report = pipeline.ingest_file(_pdf(tmp_path))
    stored = database.document_elements(report.article_id)

    assert report.status == "chunks_ready"
    assert report.element_count == 1
    assert stored[0]["original_caption"] == "Table 1. Synthetic values."
    assert stored[0]["synthetic_caption"] is None
    assert stored[0]["cells"][0]["text"] == "Treatment"
    assert stored[0]["text_relations"][0]["source_excerpt"] == relation_text
    assert stored[0]["text_relations"][0]["related_chunk_id"] is not None


def test_pipeline_applies_trusted_catalog_metadata(settings, tmp_path: Path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    pipeline = IngestionPipeline(settings, database, extractor=FakeExtractor())

    report = pipeline.ingest_file(
        _pdf(tmp_path),
        catalog_metadata=PdfCatalogMetadata(
            title="Cahier technique IFPC",
            journal="Pomme à Cidre",
            publication_year=2025,
            source="IFPC",
        ),
    )

    article = database.list_articles(limit=1)[0]
    assert report.status == "chunks_ready"
    assert article["title"] == "Cahier technique IFPC"
    assert article["journal"] == "Pomme à Cidre"
    assert article["publication_year"] == 2025
    assert article["source"] == "IFPC"


def test_pipeline_detects_same_doi_across_different_pdf_files(settings, tmp_path: Path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    extractor = FakeExtractor(doi="10.1234/shared-doi")
    pipeline = IngestionPipeline(settings, database, extractor=extractor)
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"first synthetic PDF")
    second_pdf.write_bytes(b"second synthetic PDF")

    first = pipeline.ingest_file(first_pdf)
    second = pipeline.ingest_file(second_pdf)

    assert first.status == "chunks_ready"
    assert second.status == "duplicate"
    assert second.article_id == first.article_id
    assert extractor.calls == 2
    assert len(database.list_articles(limit=10)) == 1


def test_pipeline_resumes_from_page_cache_after_database_error(settings, tmp_path: Path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    extractor = FakeExtractor()
    pipeline = IngestionPipeline(settings, database, extractor=extractor)
    pdf = _pdf(tmp_path)
    original_save = database.save_article_and_chunks

    def fail_once(*args, **kwargs):
        raise RuntimeError("synthetic database interruption")

    database.save_article_and_chunks = fail_once  # type: ignore[method-assign]
    failed = pipeline.ingest_file(pdf)
    database.save_article_and_chunks = original_save  # type: ignore[method-assign]
    resumed = pipeline.ingest_file(pdf)

    assert failed.status == "failed"
    assert resumed.status == "chunks_ready"
    assert resumed.resumed_from_cache is True
    assert extractor.calls == 1
