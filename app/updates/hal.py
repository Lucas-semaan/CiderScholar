"""Opt-in official HAL open-archive search."""

from __future__ import annotations

from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class HalClient(OfficialBibliographicClient):
    source_id = "hal"
    source_label = "HAL"
    minimum_request_delay_seconds = 1.0

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        payload = self._get_json(
            f"{self.config.hal_base_url}/",
            params={
                "q": query,
                "rows": min(max(limit, 1), 1000),
                "start": max(offset, 0),
                "wt": "json",
                "fl": (
                    "docid,halId_s,title_s,abstract_s,authFullName_s,journalTitle_s,"
                    "producedDateY_i,doiId_s,uri_s,fileMain_s,docType_s,publisher_s"
                ),
            },
        )
        response = payload.get("response")
        hits = response.get("docs") if isinstance(response, dict) else None
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
        source_id = clean_text(item.get("halId_s")) or (
            str(item["docid"]) if item.get("docid") is not None else "unknown"
        )
        return BibliographicRecord(
            source=self.source_label,
            source_id=source_id,
            title=_first_text(item.get("title_s")) or "Titre indisponible",
            authors=_text_list(item.get("authFullName_s")),
            abstract=_first_text(item.get("abstract_s")),
            journal=_first_text(item.get("journalTitle_s")),
            work_type=_first_text(item.get("docType_s")),
            publisher=_first_text(item.get("publisher_s")),
            publication_year=integer_or_none(item.get("producedDateY_i")),
            doi=normalize_doi(item.get("doiId_s")),
            citation_count=None,
            url=_first_text(item.get("uri_s")) or _first_text(item.get("fileMain_s")),
            relevance_score=None,
        )


def _first_text(value: object) -> str | None:
    values = _text_list(value)
    return values[0] if values else None


def _text_list(value: object) -> list[str]:
    raw = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(cleaned for item in raw if (cleaned := clean_text(item)) is not None))
