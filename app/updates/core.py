"""Opt-in official CORE works search with optional authenticated quota."""

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


class CoreClient(OfficialBibliographicClient):
    source_id = "core"
    source_label = "CORE"
    minimum_request_delay_seconds = 10.0

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        api_key = os.environ.get(self.config.core_api_key_env, "").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        payload = self._get_json(
            f"{self.config.core_base_url}/search/works/",
            params={
                "q": query,
                "limit": min(max(limit, 1), 100),
                "offset": max(offset, 0),
            },
            headers=headers,
        )
        hits = payload.get("results")
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
        source_id = (
            str(item["id"])
            if item.get("id") is not None
            else clean_text(item.get("doi")) or "unknown"
        )
        return BibliographicRecord(
            source=self.source_label,
            source_id=source_id,
            title=clean_text(item.get("title")) or "Titre indisponible",
            authors=_authors(item.get("authors")),
            abstract=clean_text(item.get("abstract")),
            journal=_journal(item.get("journals")),
            work_type=clean_text(item.get("documentType")),
            publisher=clean_text(item.get("publisher")),
            publication_year=integer_or_none(item.get("yearPublished")),
            doi=normalize_doi(item.get("doi")),
            citation_count=integer_or_none(item.get("citationCount")),
            url=_url(item),
            relevance_score=None,
        )


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for author in value:
        raw_name = author.get("name") if isinstance(author, dict) else author
        name = clean_text(raw_name)
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def _journal(value: object) -> str | None:
    if not isinstance(value, list):
        return clean_text(value)
    for journal in value:
        if isinstance(journal, dict):
            name = clean_text(journal.get("title") or journal.get("name"))
        else:
            name = clean_text(journal)
        if name:
            return name
    return None


def _url(item: dict[str, Any]) -> str | None:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return f"https://doi.org/{doi}"
    for field in ("downloadUrl", "sourceFulltextUrls", "links"):
        value = item.get(field)
        values = value if isinstance(value, list) else [value]
        for candidate in values:
            if isinstance(candidate, dict):
                candidate = candidate.get("url") or candidate.get("href")
            url = clean_text(candidate)
            if url and url.startswith("https://"):
                return url
    return None
