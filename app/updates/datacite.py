"""Opt-in official DataCite DOI metadata search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class DataCiteClient(OfficialBibliographicClient):
    source_id = "datacite"
    source_label = "DataCite"
    minimum_request_delay_seconds = 1.0

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        page_size = min(max(limit, 1), 100)
        payload = self._get_json(
            f"{self.config.datacite_base_url}/dois",
            params={
                "query": query,
                "page[size]": page_size,
                "page[number]": max(offset, 0) // page_size + 1,
            },
        )
        hits = payload.get("data")
        if not isinstance(hits, list):
            return []
        records: list[BibliographicRecord] = []
        for item in hits:
            if not isinstance(item, dict) or not isinstance(item.get("attributes"), dict):
                continue
            try:
                records.append(self._record(item))
            except ValueError:
                continue
        return records

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        attributes = item["attributes"]
        doi = normalize_doi(attributes.get("doi")) or normalize_doi(item.get("id"))
        types = attributes.get("types")
        types = types if isinstance(types, dict) else {}
        return BibliographicRecord(
            source=self.source_label,
            source_id=clean_text(item.get("id")) or doi or "unknown",
            title=_named_text(attributes.get("titles"), "title") or "Titre indisponible",
            authors=_creators(attributes.get("creators")),
            abstract=_abstract(attributes.get("descriptions")),
            journal=_container_title(attributes.get("container")),
            work_type=clean_text(
                types.get("resourceTypeGeneral")
                or types.get("resourceType")
                or types.get("schemaOrg")
            ),
            publisher=clean_text(attributes.get("publisher")),
            publication_year=integer_or_none(attributes.get("publicationYear")),
            doi=doi,
            citation_count=integer_or_none(attributes.get("citationCount")),
            url=clean_text(attributes.get("url")) or (f"https://doi.org/{doi}" if doi else None),
            relevance_score=None,
        )


def _named_text(value: object, field: str) -> str | None:
    if not isinstance(value, list):
        return None
    for entry in value:
        text = clean_text(entry.get(field)) if isinstance(entry, dict) else clean_text(entry)
        if text:
            return text
    return None


def _creators(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [
        name
        for creator in value
        if isinstance(creator, dict)
        and (
            name := clean_text(
                creator.get("name")
                or " ".join(str(creator.get(field) or "") for field in ("givenName", "familyName"))
            )
        )
        is not None
    ]
    return list(dict.fromkeys(names))


def _abstract(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    descriptions = [entry for entry in value if isinstance(entry, dict)]
    for entry in descriptions:
        if str(entry.get("descriptionType") or "").casefold() == "abstract":
            abstract = clean_text(entry.get("description"))
            if abstract:
                return abstract
    return _named_text(descriptions, "description")


def _container_title(value: object) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("title") or value.get("name"))
    return clean_text(value)
