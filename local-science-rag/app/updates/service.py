"""Sequential, explicit discovery across configured bibliographic sources."""

from __future__ import annotations

import re
from time import perf_counter

from app.config import Settings
from app.updates.base import OfficialBibliographicClient
from app.updates.clarivate import ClarivateClient
from app.updates.crossref import CrossrefClient
from app.updates.elsevier import ElsevierClient
from app.updates.europe_pmc import EuropePmcClient
from app.updates.models import (
    BibliographicRecord,
    BibliographicSearchReport,
    BibliographicSourceError,
)
from app.updates.openalex import OpenAlexClient

type ClientType = type[OfficialBibliographicClient]
CLIENTS: dict[str, ClientType] = {
    "crossref": CrossrefClient,
    "europe_pmc": EuropePmcClient,
    "openalex": OpenAlexClient,
    "clarivate": ClarivateClient,
    "elsevier": ElsevierClient,
}
TITLE_TOKEN = re.compile(r"[^a-z0-9]+")


class BibliographicDiscoveryService:
    """Send only an explicit query to official APIs, one source at a time."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search(
        self,
        query: str,
        *,
        limit_per_source: int | None = None,
    ) -> BibliographicSearchReport:
        cleaned_query = " ".join(query.split())
        if not cleaned_query:
            raise ValueError("bibliographic query cannot be empty")
        if len(cleaned_query) > 2000:
            raise ValueError("bibliographic query exceeds 2000 characters")
        if not self.settings.app.allow_bibliographic_apis:
            raise RuntimeError("bibliographic APIs are disabled by app configuration")
        if not self.settings.bibliographic.enabled:
            raise RuntimeError("bibliographic discovery is disabled")
        limit = limit_per_source or self.settings.bibliographic.per_source_limit
        if not 1 <= limit <= 50:
            raise ValueError("bibliographic per-source limit must be between 1 and 50")

        started = perf_counter()
        records: list[BibliographicRecord] = []
        errors: list[BibliographicSourceError] = []
        successful_sources: list[str] = []
        queried_sources = list(self.settings.bibliographic.sources)
        for source in queried_sources:
            client_type = CLIENTS[source]
            try:
                with client_type(self.settings) as client:
                    source_records = client.search(cleaned_query, limit)
                records.extend(source_records)
                successful_sources.append(source)
            except Exception as exc:
                errors.append(
                    BibliographicSourceError(
                        source=source,
                        error_type=type(exc).__name__,
                        message=str(exc)[:500],
                    )
                )
        return BibliographicSearchReport(
            query=cleaned_query,
            queried_sources=queried_sources,
            successful_sources=successful_sources,
            records=_deduplicate(records),
            errors=errors,
            duration_seconds=perf_counter() - started,
        )


def _deduplicate(records: list[BibliographicRecord]) -> list[BibliographicRecord]:
    unique: list[BibliographicRecord] = []
    seen: set[str] = set()
    for record in records:
        title_key = TITLE_TOKEN.sub("", record.title.casefold())
        key = f"doi:{record.doi}" if record.doi else f"title:{title_key}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique
