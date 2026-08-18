"""Bounded access to the public Aureli Primo search API."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.updates.base import OfficialBibliographicClient
from app.updates.models import BibliographicRecord, clean_text, normalize_doi

AURELI_API_URL = "https://aureli.inrae.fr/primaws/rest/pub/pnxs"
AURELI_DISCOVERY_URL = "https://aureli.inrae.fr/discovery/fulldisplay"
AURELI_VIEW = "33INRAE_INST:aureli"
AURELI_SCOPE = "MyInst_and_CI"
AURELI_MAX_PAGE_SIZE = 50
AURELI_GUEST_MAX_OFFSET = 200
# Primo accepts the first 2,000 authenticated results of one bounded search.
AURELI_AUTHENTICATED_MAX_OFFSET = 1_950
AURELI_SESSION_TOKEN_ENVIRONMENT = "CIDERSCHOLAR_AURELI_SESSION_TOKEN"


class AureliSearchPage(BaseModel):
    """One validated page from the Aureli result set."""

    model_config = ConfigDict(extra="forbid")

    year: int
    offset: int = Field(ge=0)
    total_results: int = Field(ge=0)
    raw_record_count: int = Field(ge=0)
    records: list[BibliographicRecord]
    parse_error_count: int = Field(ge=0)


class AureliClient(OfficialBibliographicClient):
    """Read article notices from Aureli without authentication or HTML scraping."""

    source_id = "aureli"
    source_label = "Aureli"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(settings, transport=transport)
        self._http.headers.update(
            {
                "User-Agent": "CiderScholar/0.2 (local scientific corpus curation)",
                "Referer": "https://aureli.inrae.fr/",
            }
        )
        session_token = os.environ.get(AURELI_SESSION_TOKEN_ENVIRONMENT, "").strip().strip('"')
        self.max_offset = (
            AURELI_AUTHENTICATED_MAX_OFFSET if session_token else AURELI_GUEST_MAX_OFFSET
        )
        if session_token:
            self._http.headers["Authorization"] = f"Bearer {session_token}"

    def search_articles(
        self,
        query: str,
        *,
        year: int,
        limit: int = AURELI_MAX_PAGE_SIZE,
        offset: int = 0,
    ) -> AureliSearchPage:
        """Search full text but return article metadata for one bounded year slice."""

        normalized_query = " ".join(query.split())
        if not normalized_query or any(character in normalized_query for character in ",;"):
            raise ValueError("Aureli query must be non-empty and contain no query separators")
        current_year = datetime.now(UTC).year
        if not 1600 <= year <= current_year:
            raise ValueError("Aureli publication year is outside the supported range")
        if not 1 <= limit <= AURELI_MAX_PAGE_SIZE:
            raise ValueError("Aureli page size must be between 1 and 50")
        if not 0 <= offset <= self.max_offset:
            raise ValueError(f"Aureli offset must be between 0 and {self.max_offset}")

        article_and_year = (
            f"facet_rtype,exact,articles|,|facet_searchcreationdate,exact,[{year} TO {year}]"
        )
        payload = self._get_json(
            AURELI_API_URL,
            params={
                "vid": AURELI_VIEW,
                "scope": AURELI_SCOPE,
                "tab": "Everything",
                "q": f"any,contains,{normalized_query}",
                "qInclude": article_and_year,
                "lang": "fr",
                "offset": offset,
                "limit": limit,
                "sort": "rank",
                "searchInFulltextUserSelection": "true",
                "rtaLinks": "false",
            },
        )
        info = payload.get("info")
        total = _integer((info or {}).get("total")) if isinstance(info, dict) else None
        docs = payload.get("docs")
        if not isinstance(docs, list):
            docs = []
        records: list[BibliographicRecord] = []
        parse_errors = 0
        for item in docs:
            if not isinstance(item, dict):
                parse_errors += 1
                continue
            try:
                records.append(self._record(item))
            except (TypeError, ValueError):
                parse_errors += 1
        return AureliSearchPage(
            year=year,
            offset=offset,
            total_results=max(total or 0, 0),
            raw_record_count=len(docs),
            records=records,
            parse_error_count=parse_errors,
        )

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        """Keep the shared client contract explicit: a year is required for safe paging."""

        del query, limit, offset
        raise ValueError("Aureli searches require search_articles() with an explicit year")

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        pnx = item.get("pnx")
        if not isinstance(pnx, dict):
            raise ValueError("Aureli record has no PNX payload")
        control = _mapping(pnx.get("control"))
        display = _mapping(pnx.get("display"))
        search = _mapping(pnx.get("search"))
        facets = _mapping(pnx.get("facets"))
        addata = _mapping(pnx.get("addata"))

        record_id = _first(control.get("recordid"))
        if not record_id:
            raise ValueError("Aureli record has no stable identifier")
        title = _first(addata.get("atitle")) or _first(display.get("title"))
        title = clean_text(title)
        if not title:
            raise ValueError("Aureli record has no usable title")

        authors = _strings(addata.get("au")) or _strings(search.get("creatorcontrib"))
        if not authors:
            for creator in _strings(display.get("creator")):
                authors.extend(part.strip() for part in creator.split(";") if part.strip())
        authors = list(dict.fromkeys(filter(None, (clean_text(author) for author in authors))))

        abstract = _first(addata.get("abstract"))
        if abstract is None:
            abstract = _first(display.get("description")) or _first(search.get("description"))
        abstract = clean_text(abstract)
        journal = clean_text(_first(addata.get("jtitle")) or _first(facets.get("jtitle")))
        work_type = clean_text(
            _first(display.get("type"))
            or _first(addata.get("genre"))
            or _first(search.get("rsrctype"))
            or "article"
        )
        publisher = clean_text(_first(addata.get("pub")) or _first(display.get("publisher")))
        publication_year = _publication_year(addata, search, facets)
        doi = _doi(addata, display)
        context = clean_text(item.get("context")) or "PC"
        source_parameters = {
            "docid": record_id,
            "context": context,
            "vid": AURELI_VIEW,
            "lang": "fr",
        }
        source_url = f"{AURELI_DISCOVERY_URL}?{urlencode(source_parameters)}"

        return BibliographicRecord(
            source=self.source_label,
            source_id=record_id,
            title=title,
            authors=authors,
            abstract=abstract,
            journal=journal,
            work_type=work_type,
            publisher=publisher,
            publication_year=publication_year,
            doi=doi,
            citation_count=_citation_count(item.get("extras")),
            url=source_url,
        )


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def aureli_max_offset() -> int:
    """Return the safe campaign bound without exposing the session token."""

    return (
        AURELI_AUTHENTICATED_MAX_OFFSET
        if os.environ.get(AURELI_SESSION_TOKEN_ENVIRONMENT, "").strip()
        else AURELI_GUEST_MAX_OFFSET
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first(value: object) -> str | None:
    values = _strings(value)
    return values[0] if values else None


def _integer(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _publication_year(
    addata: dict[str, Any],
    search: dict[str, Any],
    facets: dict[str, Any],
) -> int | None:
    for value in (
        _first(addata.get("risdate")),
        _first(addata.get("date")),
        _first(search.get("creationdate")),
        _first(facets.get("creationdate")),
    ):
        match = re.search(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b", value or "")
        if match:
            return int(match.group(1))
    return None


def _doi(addata: dict[str, Any], display: dict[str, Any]) -> str | None:
    for value in _strings(addata.get("doi")) + _strings(display.get("identifier")):
        doi = normalize_doi(value)
        if doi:
            return doi
    return None


def _citation_count(value: object) -> int | None:
    extras = _mapping(value)
    times_cited = extras.get("timesCited")
    if isinstance(times_cited, dict):
        for candidate in times_cited.values():
            count = _integer(candidate)
            if count is not None and count >= 0:
                return count
    return None
