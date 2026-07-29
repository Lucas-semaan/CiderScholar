"""Bounded local metadata extraction for suggestion relevance checks."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.ingestion.metadata import extract_metadata
from app.ingestion.pdf_extractor import PageText, PdfExtractor, PyMuPdfExtractor
from app.suggestions.models import (
    DoiSuggestionSource,
    ManualSuggestionSource,
    PdfSuggestionSource,
    SuggestionCandidateContext,
    SuggestionSource,
    UrlSuggestionSource,
)


def _bounded_excerpt(pages: Sequence[PageText], maximum_characters: int) -> str | None:
    combined = "\n\n".join(page.text.strip() for page in pages if page.text.strip())
    cleaned = combined[:maximum_characters].strip()
    return cleaned or None


def context_from_reference(source: SuggestionSource) -> SuggestionCandidateContext:
    if isinstance(source, (DoiSuggestionSource, UrlSuggestionSource)):
        return SuggestionCandidateContext(
            title=source.title,
            doi=source.doi if isinstance(source, DoiSuggestionSource) else None,
            abstract=source.abstract,
        )
    if isinstance(source, ManualSuggestionSource):
        return SuggestionCandidateContext(
            title=source.title,
            doi=source.doi,
            abstract=source.abstract,
            text_excerpt=source.reference,
        )
    return SuggestionCandidateContext(
        title=source.title,
        doi=source.doi,
        abstract=source.abstract,
    )


def extract_pdf_candidate(
    source: PdfSuggestionSource,
    pdf_path: Path,
    *,
    maximum_context_characters: int,
    extractor: PdfExtractor | None = None,
) -> SuggestionCandidateContext:
    """Extract only bounded text and literal metadata; never return PDF bytes."""

    extracted = (extractor or PyMuPdfExtractor()).extract(pdf_path)
    metadata = extract_metadata(
        pdf_path=pdf_path,
        document_metadata=extracted.metadata,
        pages=extracted.pages,
    )
    return SuggestionCandidateContext(
        title=source.title or metadata.title,
        doi=source.doi or metadata.doi,
        abstract=source.abstract or metadata.abstract,
        text_excerpt=_bounded_excerpt(extracted.pages, maximum_context_characters),
    )
