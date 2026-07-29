"""Sequential, resumable first-stage PDF ingestion pipeline."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.database.sqlite import Database
from app.ingestion.chunker import ScientificChunker
from app.ingestion.deduplication import sha256_file
from app.ingestion.metadata import extract_metadata
from app.ingestion.pdf_extractor import (
    ExtractedDocument,
    PdfExtractor,
    PyMuPdfExtractor,
)
from app.memory import MemoryGuard

LOGGER = logging.getLogger(__name__)


class IngestionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pdf_path: str
    sha256: str | None = None
    article_id: str | None = None
    status: Literal["chunks_ready", "duplicate", "ocr_required", "failed"]
    page_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    element_count: int = Field(default=0, ge=0)
    ocr_uncertain_page_count: int = Field(default=0, ge=0)
    resumed_from_cache: bool = False
    error_type: str | None = None
    error_message: str | None = None
    duration_seconds: float = Field(ge=0.0)


class PdfCatalogMetadata(BaseModel):
    """Trusted catalog fields applied without inferring missing scientific metadata."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$")
    abstract: str | None = Field(default=None, max_length=50000)
    authors: list[str] = Field(default_factory=list, max_length=500)
    journal: str | None = Field(default=None, max_length=500)
    publication_year: int | None = Field(default=None, ge=1600, le=2200)
    language: str | None = Field(default=None, max_length=20)
    source: str = Field(min_length=1, max_length=200)


class IngestionPipeline:
    """Ingest one PDF at a time and commit article + chunks atomically."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        extractor: PdfExtractor | None = None,
        *,
        refresh_ocr_cache: bool = False,
    ) -> None:
        self.settings = settings
        self.database = database
        self.extractor = extractor or PyMuPdfExtractor(
            min_page_text_characters=settings.ingestion.min_page_text_characters,
            min_text_page_ratio=settings.ingestion.min_text_page_ratio,
        )
        self.refresh_ocr_cache = refresh_ocr_cache
        self.chunker = ScientificChunker(
            target_tokens=settings.ingestion.target_tokens,
            max_tokens=settings.ingestion.max_tokens,
            overlap_tokens=settings.ingestion.overlap_tokens,
        )
        self.memory = MemoryGuard(settings.memory)

    def _cache_path(self, sha256: str) -> Path:
        return self.settings.paths.extracted_dir / f"{sha256}.pages.json"

    def _load_cache(self, sha256: str, pdf_path: Path) -> ExtractedDocument | None:
        cache_path = self._cache_path(sha256)
        if not cache_path.is_file():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            document = ExtractedDocument.from_dict(payload)
            if Path(document.pdf_path).resolve() != pdf_path.resolve():
                return None
            return document
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _save_cache(self, sha256: str, document: ExtractedDocument) -> None:
        destination = self._cache_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{sha256}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document.to_dict(), handle, ensure_ascii=False)
            Path(temp_name).replace(destination)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def ingest_file(
        self,
        pdf_path: str | Path,
        *,
        catalog_metadata: PdfCatalogMetadata | None = None,
    ) -> IngestionReport:
        started = datetime.now(UTC)
        path = Path(pdf_path).resolve()
        sha256: str | None = None
        page_count = 0
        resumed = False

        try:
            if not path.is_file():
                raise FileNotFoundError(f"PDF not found: {path}")
            if path.suffix.lower() != ".pdf":
                raise ValueError("ingestion accepts PDF files only")

            self.memory.check("PDF ingestion")
            sha256 = sha256_file(path)
            existing = self.database.article_by_sha256(sha256)
            if existing is not None and self.database.chunk_count(existing["id"]) > 0:
                return self._report(
                    started,
                    path,
                    sha256=sha256,
                    article_id=existing["id"],
                    status="duplicate",
                    chunk_count=self.database.chunk_count(existing["id"]),
                    element_count=self.database.document_element_count(existing["id"]),
                )

            self.database.upsert_ingestion_job(
                pdf_path=str(path),
                sha256=sha256,
                state="extracting",
                increment_attempt=True,
            )
            document = self._load_cache(sha256, path)
            if document is not None and document.requires_ocr and self.refresh_ocr_cache:
                document = None
            resumed = document is not None
            if document is None:
                document = self.extractor.extract(path)
                self._save_cache(sha256, document)
            page_count = document.page_count
            if document.ocr_pages:
                self.database.save_ocr_page_traces(
                    sha256,
                    [trace.model_dump(mode="python") for trace in document.ocr_pages],
                )
            uncertain_ocr_pages = sum(not trace.admitted for trace in document.ocr_pages)
            self.database.upsert_ingestion_job(pdf_path=str(path), sha256=sha256, state="extracted")

            if document.requires_ocr:
                self.database.upsert_ingestion_job(
                    pdf_path=str(path), sha256=sha256, state="ocr_required"
                )
                return self._report(
                    started,
                    path,
                    sha256=sha256,
                    status="ocr_required",
                    page_count=page_count,
                    ocr_uncertain_page_count=uncertain_ocr_pages,
                    resumed_from_cache=resumed,
                )

            self.memory.check("PDF chunking")
            metadata = extract_metadata(
                pdf_path=path,
                document_metadata=document.metadata,
                pages=document.pages,
                scan_pages=self.settings.ingestion.metadata_scan_pages,
            )
            if catalog_metadata is not None:
                metadata = metadata.model_copy(
                    update={
                        "doi": catalog_metadata.doi or metadata.doi,
                        "title": catalog_metadata.title,
                        "abstract": catalog_metadata.abstract or metadata.abstract,
                        "authors": catalog_metadata.authors or metadata.authors,
                        "journal": catalog_metadata.journal or metadata.journal,
                        "publication_year": (
                            catalog_metadata.publication_year or metadata.publication_year
                        ),
                        "language": catalog_metadata.language or metadata.language,
                    }
                )
            if metadata.doi:
                existing_doi = self.database.article_by_doi(metadata.doi)
                if existing_doi is not None:
                    existing_chunk_count = self.database.chunk_count(existing_doi["id"])
                    self.database.upsert_ingestion_job(
                        pdf_path=str(path),
                        sha256=sha256,
                        state="chunks_ready",
                        article_id=existing_doi["id"],
                    )
                    if document.ocr_pages:
                        self.database.save_ocr_page_traces(
                            sha256,
                            [trace.model_dump(mode="python") for trace in document.ocr_pages],
                            article_id=str(existing_doi["id"]),
                        )
                    return self._report(
                        started,
                        path,
                        sha256=sha256,
                        article_id=existing_doi["id"],
                        status="duplicate",
                        page_count=page_count,
                        chunk_count=existing_chunk_count,
                        element_count=self.database.document_element_count(existing_doi["id"]),
                        ocr_uncertain_page_count=uncertain_ocr_pages,
                        resumed_from_cache=resumed,
                    )
            self.database.upsert_ingestion_job(pdf_path=str(path), sha256=sha256, state="chunking")
            chunks = self.chunker.chunk(document.pages)
            if not chunks:
                raise ValueError("no chunks produced from extracted text")

            article_id = str(uuid.uuid4())
            self.database.upsert_ingestion_job(
                pdf_path=str(path), sha256=sha256, state="persisting"
            )
            article = {
                **metadata.model_dump(mode="python"),
                "id": article_id,
                "sha256": sha256,
                "pdf_path": str(path),
                "validation_status": self.settings.ingestion.local_import_validation_status,
                "source": catalog_metadata.source if catalog_metadata else "local",
            }
            self.database.save_article_and_chunks(
                article,
                [chunk.model_dump(mode="python") for chunk in chunks],
                [element.model_dump(mode="python") for element in document.elements],
            )
            if document.ocr_pages:
                self.database.save_ocr_page_traces(
                    sha256,
                    [trace.model_dump(mode="python") for trace in document.ocr_pages],
                    article_id=article_id,
                )
            self.database.upsert_ingestion_job(
                pdf_path=str(path),
                sha256=sha256,
                state="chunks_ready",
                article_id=article_id,
            )
            self.memory.check("PDF persistence")
            return self._report(
                started,
                path,
                sha256=sha256,
                article_id=article_id,
                status="chunks_ready",
                page_count=page_count,
                chunk_count=len(chunks),
                element_count=len(document.elements),
                ocr_uncertain_page_count=uncertain_ocr_pages,
                resumed_from_cache=resumed,
            )
        except Exception as exc:
            error_type = type(exc).__name__
            # Error messages are technical only; extracted article text is never logged.
            error_message = str(exc)[:1000]
            LOGGER.error(
                "Ingestion failed pdf=%s sha256=%s error_type=%s",
                path.name,
                sha256,
                error_type,
            )
            if sha256 is not None:
                self.database.upsert_ingestion_job(
                    pdf_path=str(path),
                    sha256=sha256,
                    state="failed",
                    error_type=error_type,
                    error_message=error_message,
                )
            return self._report(
                started,
                path,
                sha256=sha256,
                status="failed",
                page_count=page_count,
                resumed_from_cache=resumed,
                error_type=error_type,
                error_message=error_message,
            )

    @staticmethod
    def _report(
        started: datetime,
        path: Path,
        *,
        status: Literal["chunks_ready", "duplicate", "ocr_required", "failed"],
        sha256: str | None = None,
        article_id: str | None = None,
        page_count: int = 0,
        chunk_count: int = 0,
        element_count: int = 0,
        ocr_uncertain_page_count: int = 0,
        resumed_from_cache: bool = False,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> IngestionReport:
        return IngestionReport(
            pdf_path=str(path),
            sha256=sha256,
            article_id=article_id,
            status=status,
            page_count=page_count,
            chunk_count=chunk_count,
            element_count=element_count,
            ocr_uncertain_page_count=ocr_uncertain_page_count,
            resumed_from_cache=resumed_from_cache,
            error_type=error_type,
            error_message=error_message,
            duration_seconds=(datetime.now(UTC) - started).total_seconds(),
        )
