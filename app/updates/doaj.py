"""Opt-in official Directory of Open Access Journals article search."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class DoajClient(OfficialBibliographicClient):
    source_id = "doaj"
    source_label = "DOAJ"
    minimum_request_delay_seconds = 1.0

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        page_size = min(max(limit, 1), 100)
        encoded_query = quote(query, safe=':()"')
        payload = self._get_json(
            f"{self.config.doaj_base_url}/{encoded_query}",
            params={
                "page": max(offset, 0) // page_size + 1,
                "pageSize": page_size,
            },
        )
        hits = payload.get("results")
        if not isinstance(hits, list):
            return []
        records: list[BibliographicRecord] = []
        for item in hits:
            if not isinstance(item, dict) or not isinstance(item.get("bibjson"), dict):
                continue
            try:
                records.append(self._record(item))
            except ValueError:
                continue
        return records

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        bibjson = item["bibjson"]
        journal = bibjson.get("journal")
        journal = journal if isinstance(journal, dict) else {}
        doi = _doi(bibjson.get("identifier"))
        return BibliographicRecord(
            source=self.source_label,
            source_id=clean_text(item.get("id")) or doi or "unknown",
            title=clean_text(bibjson.get("title")) or "Titre indisponible",
            authors=_authors(bibjson.get("author")),
            abstract=clean_text(bibjson.get("abstract")),
            journal=clean_text(journal.get("title")),
            work_type="journal-article",
            publisher=clean_text(journal.get("publisher")),
            publication_year=integer_or_none(bibjson.get("year")),
            doi=doi,
            citation_count=None,
            url=_url(bibjson.get("link"), doi),
            relevance_score=None,
        )


def _doi(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for identifier in value:
        if not isinstance(identifier, dict) or str(identifier.get("type")).casefold() != "doi":
            continue
        doi = normalize_doi(identifier.get("id"))
        if doi:
            return doi
    return None


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [
        name
        for author in value
        if isinstance(author, dict) and (name := clean_text(author.get("name"))) is not None
    ]
    return list(dict.fromkeys(names))


def _url(value: object, doi: str | None) -> str | None:
    if isinstance(value, list):
        for link in value:
            if not isinstance(link, dict):
                continue
            url = clean_text(link.get("url"))
            if url and url.startswith("https://"):
                return url
    return f"https://doi.org/{doi}" if doi else None
