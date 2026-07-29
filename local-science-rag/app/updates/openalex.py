"""Opt-in official OpenAlex works search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class OpenAlexClient(OfficialBibliographicClient):
    source_id = "openalex"
    source_label = "OpenAlex"
    api_key_environment_attribute = "openalex_api_key_env"

    def rate_limit_status(self) -> dict[str, Any]:
        payload = self._get_json(
            f"{self.config.openalex_base_url}/rate-limit",
            params={"api_key": self.api_key()},
        )
        rate_limit = payload.get("rate_limit")
        if not isinstance(rate_limit, dict):
            raise ValueError("OpenAlex returned invalid rate-limit metadata")
        return rate_limit

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        page_size = min(max(limit, 1), 100)
        payload = self._get_json(
            f"{self.config.openalex_base_url}/works",
            params={
                "search": query,
                "per_page": page_size,
                "page": max(offset, 0) // page_size + 1,
                "sort": "relevance_score:desc",
                "api_key": self.api_key(),
            },
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            return []
        return [self._record(item) for item in raw_results if isinstance(item, dict)]

    def lookup_dois(self, dois: list[str]) -> list[BibliographicRecord]:
        """Resolve up to 100 DOI values in one low-cost filtered request."""

        unique_dois = list(dict.fromkeys(doi.strip().lower() for doi in dois if doi.strip()))
        if not unique_dois:
            return []
        if len(unique_dois) > 100:
            raise ValueError("OpenAlex DOI lookup accepts at most 100 DOI values")
        payload = self._get_json(
            f"{self.config.openalex_base_url}/works",
            params={
                "filter": f"doi:{'|'.join(unique_dois)}",
                "per_page": len(unique_dois),
                "api_key": self.api_key(),
            },
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            return []
        return [self._record(item) for item in raw_results if isinstance(item, dict)]

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        authors: list[str] = []
        for authorship in item.get("authorships") or []:
            if not isinstance(authorship, dict):
                continue
            author = authorship.get("author")
            if isinstance(author, dict) and isinstance(author.get("display_name"), str):
                authors.append(author["display_name"].strip())
        primary = (
            item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
        )
        source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
        source_id = str(item.get("id") or "unknown").rsplit("/", 1)[-1]
        title = clean_text(item.get("title") or item.get("display_name"))
        return BibliographicRecord(
            source=self.source_label,
            source_id=source_id,
            title=title or "Titre indisponible",
            authors=list(dict.fromkeys(author for author in authors if author)),
            abstract=_abstract(item.get("abstract_inverted_index")),
            journal=clean_text(source.get("display_name")),
            publication_year=integer_or_none(item.get("publication_year")),
            doi=normalize_doi(item.get("doi")),
            citation_count=integer_or_none(item.get("cited_by_count")),
            url=_first_string(item.get("doi"), primary.get("landing_page_url"), item.get("id")),
            relevance_score=_float_or_none(item.get("relevance_score")),
        )


def _abstract(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    positions: list[tuple[int, str]] = []
    for word, indexes in value.items():
        if not isinstance(word, str) or not isinstance(indexes, list):
            continue
        positions.extend((index, word) for index in indexes if isinstance(index, int))
    return clean_text(" ".join(word for _, word in sorted(positions)))


def _first_string(*values: object) -> str | None:
    return next(
        (value.strip() for value in values if isinstance(value, str) and value.strip()),
        None,
    )


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
