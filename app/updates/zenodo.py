"""Opt-in official Zenodo publication search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import BibliographicRecord, clean_text, integer_or_none, normalize_doi


class ZenodoClient(OfficialBibliographicClient):
    source_id = "zenodo"
    source_label = "Zenodo"
    # Zenodo limits anonymous record searches to 25 hits per request and the
    # search endpoint to 30 requests per minute.  Keep a small safety margin so
    # one campaign cannot turn valid searches into avoidable 400/429 responses.
    minimum_request_delay_seconds = 2.1

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        requested_page_size = max(limit, 1)
        page_size = min(requested_page_size, 25)
        payload = self._get_json(
            f"{self.config.zenodo_base_url}/records",
            params={
                "q": query,
                "type": "publication",
                # Harvest checkpoints advance by the requested logical page
                # size.  Dividing by that value keeps pages contiguous even
                # though anonymous Zenodo requests must be capped at 25 hits.
                "page": max(offset, 0) // requested_page_size + 1,
                "size": page_size,
            },
        )
        hits_container = payload.get("hits")
        hits = hits_container.get("hits") if isinstance(hits_container, dict) else None
        if not isinstance(hits, list):
            return []
        records: list[BibliographicRecord] = []
        for item in hits:
            if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
                continue
            try:
                records.append(self._record(item))
            except ValueError:
                continue
        return records

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        metadata = item["metadata"]
        doi = normalize_doi(item.get("doi")) or normalize_doi(metadata.get("doi"))
        journal = metadata.get("journal")
        journal = journal if isinstance(journal, dict) else {}
        resource_type = metadata.get("resource_type")
        resource_type = resource_type if isinstance(resource_type, dict) else {}
        return BibliographicRecord(
            source=self.source_label,
            source_id=str(item["id"]) if item.get("id") is not None else doi or "unknown",
            title=clean_text(metadata.get("title") or item.get("title")) or "Titre indisponible",
            authors=_creators(metadata.get("creators")),
            abstract=clean_text(metadata.get("description")),
            journal=clean_text(journal.get("title")),
            work_type=clean_text(
                resource_type.get("title")
                or resource_type.get("subtype")
                or resource_type.get("type")
            ),
            publisher=clean_text(metadata.get("publisher")),
            publication_year=_year(metadata.get("publication_date")),
            doi=doi,
            citation_count=None,
            url=_url(item.get("links"), doi),
            relevance_score=None,
        )


def _creators(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [
        name
        for creator in value
        if isinstance(creator, dict) and (name := clean_text(creator.get("name"))) is not None
    ]
    return list(dict.fromkeys(names))


def _year(value: object) -> int | None:
    cleaned = clean_text(value)
    return integer_or_none(cleaned[:4]) if cleaned and len(cleaned) >= 4 else None


def _url(value: object, doi: str | None) -> str | None:
    if isinstance(value, dict):
        for field in ("self_html", "doi", "self_doi_html"):
            url = clean_text(value.get(field))
            if url and url.startswith("https://"):
                return url
    return f"https://doi.org/{doi}" if doi else None
