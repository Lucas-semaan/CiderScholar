"""Opt-in official OpenAIRE Graph publication search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import BibliographicRecord, clean_text, integer_or_none, normalize_doi


class OpenAireClient(OfficialBibliographicClient):
    source_id = "openaire"
    source_label = "OpenAIRE"
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
            f"{self.config.openaire_base_url}/research-products",
            params={
                "search": query,
                "type": "publication",
                "page": max(offset, 0) // page_size + 1,
                "pageSize": page_size,
            },
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
        doi = _doi(item.get("pids")) or _instance_doi(item.get("instances"))
        source_id = clean_text(item.get("id")) or doi or "unknown"
        return BibliographicRecord(
            source=self.source_label,
            source_id=source_id,
            title=clean_text(item.get("mainTitle")) or "Titre indisponible",
            authors=_authors(item.get("authors")),
            abstract=_first_text(item.get("descriptions")),
            journal=_container_title(item.get("container")),
            work_type=_instance_type(item.get("instances")) or clean_text(item.get("type")),
            publisher=_first_text(item.get("publisher")),
            publication_year=_year(item.get("publicationDate")),
            doi=doi,
            citation_count=_citation_count(item.get("indicators")),
            url=(f"https://doi.org/{doi}" if doi else _instance_url(item.get("instances"))),
            relevance_score=None,
        )


def _doi(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for identifier in value:
        if not isinstance(identifier, dict):
            continue
        if str(identifier.get("scheme") or "").casefold() != "doi":
            continue
        doi = normalize_doi(identifier.get("value"))
        if doi:
            return doi
    return None


def _instance_doi(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    return next(
        (
            doi
            for instance in value
            if isinstance(instance, dict)
            if (doi := _doi(instance.get("pids")))
        ),
        None,
    )


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [
        name
        for author in value
        if isinstance(author, dict) and (name := clean_text(author.get("fullName"))) is not None
    ]
    return list(dict.fromkeys(names))


def _first_text(value: object) -> str | None:
    values = value if isinstance(value, list) else [value]
    for entry in values:
        if isinstance(entry, dict):
            entry = entry.get("value") or entry.get("name") or entry.get("title")
        text = clean_text(entry)
        if text:
            return text
    return None


def _container_title(value: object) -> str | None:
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("title"))
    return _first_text(value)


def _instance_type(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    return next(
        (
            work_type
            for instance in value
            if isinstance(instance, dict)
            if (work_type := clean_text(instance.get("type")))
        ),
        None,
    )


def _instance_url(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for instance in value:
        if not isinstance(instance, dict):
            continue
        urls = instance.get("urls")
        if not isinstance(urls, list):
            continue
        for candidate in urls:
            url = clean_text(candidate)
            if url and url.startswith("https://"):
                return url
    return None


def _year(value: object) -> int | None:
    cleaned = clean_text(value)
    return integer_or_none(cleaned[:4]) if cleaned and len(cleaned) >= 4 else None


def _citation_count(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    impact = value.get("citationImpact")
    return integer_or_none(impact.get("citationCount")) if isinstance(impact, dict) else None
