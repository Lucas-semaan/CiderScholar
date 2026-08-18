"""Opt-in official Crossref works search."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.updates.base import (
    BibliographicApiDeferred,
    BibliographicApiError,
    OfficialBibliographicClient,
)
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class CrossrefClient(OfficialBibliographicClient):
    source_id = "crossref"
    source_label = "Crossref"

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        params: dict[str, str | int] = {
            "query.bibliographic": query,
            "rows": min(max(limit, 1), 100),
            "offset": max(offset, 0),
            "sort": "score",
            "order": "desc",
        }
        if self.config.crossref_email:
            params["mailto"] = self.config.crossref_email
        payload = self._get_json(
            f"{self.config.crossref_base_url}/works",
            params=params,
            headers={"User-Agent": self._user_agent()},
        )
        message = payload.get("message")
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return []
        records: list[BibliographicRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                records.append(self._record(item))
            except ValueError:
                continue
        return records

    def lookup_dois(self, dois: list[str]) -> list[BibliographicRecord]:
        """Resolve up to 100 normalized DOI values through exact Crossref work endpoints."""

        normalized = list(
            dict.fromkeys(doi for value in dois if (doi := normalize_doi(value)) is not None)
        )
        if len(normalized) > 100:
            raise ValueError("Crossref DOI lookup accepts at most 100 DOI values")
        records: list[BibliographicRecord] = []
        for doi in normalized:
            try:
                payload = self._get_json(
                    f"{self.config.crossref_base_url}/works/{quote(doi, safe='')}",
                    params=(
                        {"mailto": self.config.crossref_email} if self.config.crossref_email else {}
                    ),
                    headers={"User-Agent": self._user_agent()},
                )
            except BibliographicApiDeferred:
                raise
            except BibliographicApiError:
                continue
            item = payload.get("message")
            if not isinstance(item, dict):
                continue
            try:
                record = self._record(item)
            except ValueError:
                continue
            if record.doi == doi:
                records.append(record)
        return records

    def _user_agent(self) -> str:
        if self.config.crossref_email:
            return f"LocalScienceRAG/0.1 (mailto:{self.config.crossref_email})"
        return "LocalScienceRAG/0.1 (local bibliographic assistant)"

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        authors: list[str] = []
        for author in item.get("author") or []:
            if not isinstance(author, dict):
                continue
            name = " ".join(
                part
                for part in (
                    str(author.get("given") or "").strip(),
                    str(author.get("family") or "").strip(),
                )
                if part
            )
            if name:
                authors.append(name)
        return BibliographicRecord(
            source=self.source_label,
            source_id=str(item.get("DOI") or item.get("URL") or "unknown"),
            title=_first_text(item.get("title")) or "Titre indisponible",
            authors=list(dict.fromkeys(authors)),
            abstract=clean_text(item.get("abstract")),
            journal=_first_text(item.get("container-title")),
            work_type=clean_text(item.get("type")),
            publisher=clean_text(item.get("publisher")),
            publication_year=_year(item),
            doi=normalize_doi(item.get("DOI")),
            citation_count=integer_or_none(item.get("is-referenced-by-count")),
            url=clean_text(item.get("URL")),
            relevance_score=_float_or_none(item.get("score")),
        )


def _first_text(value: object) -> str | None:
    if isinstance(value, list):
        for item in value:
            cleaned = clean_text(item)
            if cleaned:
                return cleaned
    return clean_text(value)


def _year(item: dict[str, Any]) -> int | None:
    for field in ("published-print", "published-online", "issued", "created"):
        value = item.get(field)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            year = integer_or_none(parts[0][0])
            if year is not None:
                return year
    return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
