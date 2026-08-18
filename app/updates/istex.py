"""Opt-in official ISTEX document search; metadata search is public."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class IstexClient(OfficialBibliographicClient):
    source_id = "istex"
    source_label = "ISTEX"

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        payload = self._get_json(
            f"{self.config.istex_base_url}/document/",
            params={
                "q": query,
                "size": min(max(limit, 1), 5000),
                "from": max(offset, 0),
                "output": (
                    "id,title,abstract,author,publicationDate,doi,host,genre,score,fulltext"
                ),
            },
        )
        hits = payload.get("hits")
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
        source_id = clean_text(item.get("id")) or "unknown"
        host = item.get("host")
        host = host if isinstance(host, dict) else {}
        doi = _first_doi(item.get("doi"))
        return BibliographicRecord(
            source=self.source_label,
            source_id=source_id,
            title=_first_text(item.get("title")) or "Titre indisponible",
            authors=_authors(item.get("author")),
            abstract=_first_text(item.get("abstract")),
            journal=_first_text(host.get("title")),
            work_type=_first_text(item.get("genre")),
            publisher=None,
            publication_year=_year(item.get("publicationDate") or host.get("publicationDate")),
            doi=doi,
            citation_count=None,
            url=(
                f"https://doi.org/{doi}"
                if doi
                else f"{self.config.istex_base_url}/document/{source_id}"
            ),
            relevance_score=_float_or_none(item.get("score")),
        )


def _first_text(value: object) -> str | None:
    values = value if isinstance(value, list) else [value]
    return next((text for item in values if (text := clean_text(item)) is not None), None)


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [
        name
        for author in value
        if isinstance(author, dict) and (name := clean_text(author.get("name"))) is not None
    ]
    return list(dict.fromkeys(names))


def _first_doi(value: object) -> str | None:
    values = value if isinstance(value, list) else [value]
    return next((doi for item in values if (doi := normalize_doi(item)) is not None), None)


def _year(value: object) -> int | None:
    cleaned = clean_text(value)
    return integer_or_none(cleaned[:4]) if cleaned and len(cleaned) >= 4 else None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
