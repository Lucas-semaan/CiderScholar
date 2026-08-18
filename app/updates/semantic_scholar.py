"""Opt-in official Semantic Scholar Academic Graph paper search."""

from __future__ import annotations

import os
from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class SemanticScholarClient(OfficialBibliographicClient):
    source_id = "semantic_scholar"
    source_label = "Semantic Scholar"
    minimum_request_delay_seconds = 1.0

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        api_key = os.environ.get(self.config.semantic_scholar_api_key_env, "").strip()
        headers = {"x-api-key": api_key} if api_key else None
        payload = self._get_json(
            f"{self.config.semantic_scholar_base_url}/paper/search",
            params={
                "query": query,
                "limit": min(max(limit, 1), 100),
                "offset": max(offset, 0),
                "fields": (
                    "title,abstract,authors,venue,year,externalIds,citationCount,url,"
                    "publicationTypes,openAccessPdf"
                ),
            },
            headers=headers,
        )
        hits = payload.get("data")
        if not isinstance(hits, list):
            return []
        records: list[BibliographicRecord] = []
        for item in hits:
            if not isinstance(item, dict):
                continue
            try:
                records.append(self._record(item))
            except ValueError:
                continue
        return records

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        external_ids = item.get("externalIds")
        external_ids = external_ids if isinstance(external_ids, dict) else {}
        paper_id = clean_text(item.get("paperId"))
        doi = normalize_doi(external_ids.get("DOI"))
        return BibliographicRecord(
            source=self.source_label,
            source_id=paper_id or doi or "unknown",
            title=clean_text(item.get("title")) or "Titre indisponible",
            authors=_authors(item.get("authors")),
            abstract=clean_text(item.get("abstract")),
            journal=clean_text(item.get("venue")),
            work_type=_work_type(item.get("publicationTypes")),
            publisher=None,
            publication_year=integer_or_none(item.get("year")),
            doi=doi,
            citation_count=integer_or_none(item.get("citationCount")),
            url=_url(item, doi),
            relevance_score=None,
        )


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [
        name
        for author in value
        if isinstance(author, dict) and (name := clean_text(author.get("name"))) is not None
    ]
    return list(dict.fromkeys(names))


def _work_type(value: object) -> str | None:
    if isinstance(value, list):
        return clean_text(", ".join(str(item) for item in value if item))
    return clean_text(value)


def _url(item: dict[str, Any], doi: str | None) -> str | None:
    url = clean_text(item.get("url"))
    if url and url.startswith("https://"):
        return url
    pdf = item.get("openAccessPdf")
    pdf_url = clean_text(pdf.get("url")) if isinstance(pdf, dict) else None
    if pdf_url and pdf_url.startswith("https://"):
        return pdf_url
    return f"https://doi.org/{doi}" if doi else None
