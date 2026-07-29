"""Opt-in official Elsevier Scopus metadata search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class ElsevierClient(OfficialBibliographicClient):
    source_id = "elsevier"
    source_label = "Elsevier / Scopus"
    api_key_environment_attribute = "elsevier_api_key_env"

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        payload = self._get_json(
            self.config.elsevier_base_url,
            params={
                "query": f"TITLE-ABS-KEY({query})",
                "count": min(max(limit, 1), 25),
                "start": max(offset, 0),
                "sort": "relevancy",
            },
            headers={
                "Accept": "application/json",
                "X-ELS-APIKey": self.api_key(),
            },
        )
        search_results = payload.get("search-results")
        entries = search_results.get("entry") if isinstance(search_results, dict) else None
        if not isinstance(entries, list):
            return []
        return [self._record(item) for item in entries if isinstance(item, dict)]

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        creator = clean_text(item.get("dc:creator"))
        url = clean_text(item.get("prism:url")) or _entry_url(item.get("link"))
        return BibliographicRecord(
            source=self.source_label,
            source_id=str(item.get("dc:identifier") or url or "unknown"),
            title=clean_text(item.get("dc:title")) or "Titre indisponible",
            authors=[creator] if creator else [],
            abstract=clean_text(item.get("dc:description")),
            journal=clean_text(item.get("prism:publicationName")),
            publication_year=_year(item.get("prism:coverDate")),
            doi=normalize_doi(item.get("prism:doi")) or normalize_doi(url),
            citation_count=integer_or_none(item.get("citedby-count")),
            url=url,
            relevance_score=None,
        )


def _entry_url(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for preferred in ("scopus", "self"):
        for link in value:
            if not isinstance(link, dict) or link.get("@ref") != preferred:
                continue
            url = clean_text(link.get("@href"))
            if url:
                return url
    return None


def _year(value: object) -> int | None:
    if not isinstance(value, str) or len(value) < 4:
        return None
    return integer_or_none(value[:4])
