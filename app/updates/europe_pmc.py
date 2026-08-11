"""Opt-in official Europe PMC publication search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class EuropePmcClient(OfficialBibliographicClient):
    source_id = "europe_pmc"
    source_label = "Europe PMC"

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        page_size = min(max(limit, 1), 100)
        payload = self._get_json(
            f"{self.config.europe_pmc_base_url}/search",
            params={
                "query": query,
                "resultType": "core",
                "pageSize": page_size,
                "page": max(offset, 0) // page_size + 1,
                "format": "json",
            },
        )
        result_list = payload.get("resultList")
        results = result_list.get("result") if isinstance(result_list, dict) else None
        if not isinstance(results, list):
            return []
        return [self._record(item) for item in results if isinstance(item, dict)]

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        authors = _authors(item)
        source_id = str(
            item.get("id") or item.get("pmcid") or item.get("pmid") or item.get("doi") or "unknown"
        )
        return BibliographicRecord(
            source=self.source_label,
            source_id=source_id,
            title=clean_text(item.get("title")) or "Titre indisponible",
            authors=authors,
            abstract=clean_text(item.get("abstractText")),
            journal=clean_text(item.get("journalTitle")),
            work_type=clean_text(item.get("pubType")),
            publication_year=integer_or_none(item.get("pubYear")),
            doi=normalize_doi(item.get("doi")),
            citation_count=integer_or_none(item.get("citedByCount")),
            url=_record_url(item),
            relevance_score=None,
        )


def _authors(item: dict[str, Any]) -> list[str]:
    author_list = item.get("authorList")
    authors = author_list.get("author") if isinstance(author_list, dict) else None
    names: list[str] = []
    if isinstance(authors, list):
        for author in authors:
            if not isinstance(author, dict):
                continue
            name = clean_text(
                author.get("fullName")
                or " ".join(str(author.get(field) or "") for field in ("firstName", "lastName"))
            )
            if name:
                names.append(name)
    if names:
        return list(dict.fromkeys(names))
    author_string = clean_text(item.get("authorString"))
    return [part.strip() for part in author_string.split(",")] if author_string else []


def _record_url(item: dict[str, Any]) -> str | None:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return f"https://doi.org/{doi}"
    pmcid = clean_text(item.get("pmcid"))
    if pmcid:
        return f"https://europepmc.org/article/PMC/{pmcid.removeprefix('PMC')}"
    pmid = clean_text(item.get("pmid"))
    return f"https://europepmc.org/article/MED/{pmid}" if pmid else None
