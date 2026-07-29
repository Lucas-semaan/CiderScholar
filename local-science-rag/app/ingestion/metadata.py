"""Conservative PDF metadata and DOI extraction without model inference."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.ingestion.pdf_extractor import PageText
from app.models.article import ArticleMetadata

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20|21)\d{2})(?!\d)")
ABSTRACT_PATTERN = re.compile(
    r"(?is)\b(?:abstract|résumé)\b\s*[:.-]?\s*(.+?)"
    r"(?=\n\s*(?:introduction|keywords?|mots[- ]clés)\b)"
)


def _clean_doi(candidate: str) -> str | None:
    value = candidate.strip().rstrip(".,;:")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    value = value.rstrip("]}")
    return value.lower() if DOI_PATTERN.fullmatch(value) else None


def extract_doi(texts: Sequence[str]) -> str | None:
    """Return only a DOI literally present in source text."""

    for text in texts:
        for match in DOI_PATTERN.finditer(text or ""):
            doi = _clean_doi(match.group(0))
            if doi:
                return doi
    return None


def _first_meaningful_line(pages: Sequence[PageText], fallback: str) -> str:
    for page in pages[:2]:
        for line in page.text.splitlines():
            candidate = " ".join(line.split()).strip()
            if 8 <= len(candidate) <= 500 and not candidate.lower().startswith("doi"):
                return candidate
    return fallback


def _extract_authors(raw: str | None) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace("\n", " ").strip()
    parts = re.split(r"\s*;\s*|\s+and\s+|\s+et\s+", normalized, flags=re.I)
    return [part.strip() for part in parts if part.strip()]


def _detect_language(text: str) -> str | None:
    words = set(re.findall(r"[a-zà-ÿ]+", text.lower()))
    if not words:
        return None
    french = len(words & {"le", "la", "les", "des", "une", "dans", "résultats", "étude"})
    english = len(words & {"the", "and", "of", "in", "results", "study", "this", "with"})
    if french == english == 0:
        return None
    return "fr" if french > english else "en"


def extract_metadata(
    *,
    pdf_path: Path,
    document_metadata: Mapping[str, str],
    pages: Sequence[PageText],
    scan_pages: int = 3,
) -> ArticleMetadata:
    source_pages = pages[:scan_pages]
    source_text = "\n".join(page.text for page in source_pages)
    raw_title = (document_metadata.get("title") or "").strip()
    title = raw_title or _first_meaningful_line(source_pages, pdf_path.stem)

    doi_sources = [
        document_metadata.get("doi", ""),
        document_metadata.get("subject", ""),
        document_metadata.get("keywords", ""),
        source_text,
    ]
    doi = extract_doi(doi_sources)

    abstract_match = ABSTRACT_PATTERN.search(source_text)
    abstract = " ".join(abstract_match.group(1).split()) if abstract_match else None

    year = None
    for candidate in (
        document_metadata.get("creationDate", ""),
        document_metadata.get("modDate", ""),
        source_text,
    ):
        match = YEAR_PATTERN.search(candidate)
        if match:
            year = int(match.group(1))
            break

    return ArticleMetadata(
        doi=doi,
        title=" ".join(title.split())[:500],
        abstract=abstract,
        authors=_extract_authors(document_metadata.get("author")),
        journal=(document_metadata.get("journal") or None),
        publication_year=year,
        language=_detect_language(source_text),
        pdf_path=pdf_path.resolve(),
    )
