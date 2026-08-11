"""Opt-in official Clarivate Web of Science Starter/Expanded search."""

from __future__ import annotations

import re
from typing import Any

from app.updates.base import OfficialBibliographicClient
from app.updates.models import (
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)


class ClarivateClient(OfficialBibliographicClient):
    source_id = "clarivate"
    source_label = "Clarivate Web of Science"
    api_key_environment_attribute = "clarivate_api_key_env"

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        if self.config.clarivate_api_mode == "expanded":
            return self._search_expanded(query, limit, offset=offset)
        return self._search_starter(query, limit, offset=offset)

    def _search_starter(
        self,
        query: str,
        limit: int,
        *,
        offset: int,
    ) -> list[BibliographicRecord]:
        page_size = min(max(limit, 1), 50)
        payload = self._get_json(
            f"{self.config.clarivate_base_url}/documents",
            params={
                "q": query,
                "db": self.config.clarivate_database,
                "limit": page_size,
                "page": max(offset, 0) // page_size + 1,
                "sortField": "RS+D",
            },
            headers={"X-ApiKey": self.api_key(), "Accept": "application/json"},
        )
        hits = payload.get("hits") or payload.get("records")
        if not isinstance(hits, list):
            return []
        return [self._record(item) for item in hits if isinstance(item, dict)]

    def _search_expanded(
        self,
        query: str,
        limit: int,
        *,
        offset: int,
    ) -> list[BibliographicRecord]:
        wos_query = query if re.match(r"^[A-Z]{2}=", query) else f"TS=({query})"
        payload = self._get_json(
            self.config.clarivate_base_url,
            params={
                "databaseId": self.config.clarivate_database,
                "usrQuery": wos_query,
                "count": min(max(limit, 1), 100),
                "firstRecord": max(offset, 0) + 1,
                "lang": "en",
                "sortField": "RS+D",
                "optionView": self.config.clarivate_expanded_option_view,
            },
            headers={"X-ApiKey": self.api_key(), "Accept": "application/json"},
        )
        return [self._expanded_record(item) for item in _expanded_records(payload)]

    def _record(self, item: dict[str, Any]) -> BibliographicRecord:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        names = item.get("names") if isinstance(item.get("names"), dict) else {}
        identifiers = item.get("identifiers") if isinstance(item.get("identifiers"), dict) else {}
        links = item.get("links") if isinstance(item.get("links"), dict) else {}
        return BibliographicRecord(
            source=self.source_label,
            source_id=str(item.get("uid") or item.get("id") or "unknown"),
            title=clean_text(item.get("title")) or "Titre indisponible",
            authors=_authors(names.get("authors")),
            abstract=clean_text(
                item.get("abstract") or item.get("summary") or item.get("abstractText")
            ),
            journal=clean_text(source.get("sourceTitle") or source.get("source_title")),
            work_type=_first_text(item.get("documentTypes"), item.get("documentType")),
            publication_year=integer_or_none(
                source.get("publishYear") or source.get("publish_year") or item.get("year")
            ),
            doi=normalize_doi(identifiers.get("doi") or item.get("doi")),
            citation_count=_citation_count(item.get("citations")),
            url=clean_text(links.get("record")),
            relevance_score=None,
        )

    def _expanded_record(self, item: dict[str, Any]) -> BibliographicRecord:
        titles = _dig(item, "static_data.summary.titles.title")
        pub_info = _dig(item, "static_data.summary.pub_info")
        identifiers = _dig(item, "dynamic_data.cluster_related.identifiers.identifier")
        return BibliographicRecord(
            source=f"{self.source_label} Expanded",
            source_id=str(item.get("UID") or item.get("uid") or "unknown"),
            title=(_typed_text(titles, "item") or _first_text(titles) or "Titre indisponible"),
            authors=_expanded_authors(_dig(item, "static_data.summary.names.name")),
            abstract=_first_text(
                _dig(
                    item,
                    "static_data.fullrecord_metadata.abstracts.abstract.abstract_text.p",
                )
            ),
            journal=_typed_text(titles, "source"),
            work_type=_first_text(_dig(item, "static_data.summary.pub_info.pubtype")),
            publication_year=_year_value(_dig(pub_info, "pubyear") or _dig(pub_info, "sortdate")),
            doi=_doi_from_identifiers(identifiers),
            citation_count=_expanded_citation_count(
                _dig(item, "dynamic_data.citation_related.tc_list.silo_tc")
            ),
            url=None,
            relevance_score=None,
        )


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for author in value:
        if isinstance(author, str):
            name = clean_text(author)
        elif isinstance(author, dict):
            name = clean_text(
                author.get("displayName")
                or author.get("display_name")
                or author.get("wosStandard")
                or author.get("wos_standard")
            )
        else:
            name = None
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def _citation_count(value: object) -> int | None:
    if not isinstance(value, list):
        return None
    counts: list[int] = []
    for citation in value:
        if not isinstance(citation, dict):
            continue
        count = integer_or_none(citation.get("count"))
        if count is not None:
            if str(citation.get("db") or "").upper() in {"WOS", "WOK"}:
                return count
            counts.append(count)
    return max(counts) if counts else None


def _expanded_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Handle the record wrappers returned by Expanded API variants."""

    candidates = [
        _dig(payload, "Data.Records.records.REC"),
        _dig(payload, "Data.Records.records"),
        _dig(payload, "Data.Records.REC"),
        _dig(payload, "Data.records"),
    ]
    for candidate in candidates:
        records = [item for item in _as_list(candidate) if isinstance(item, dict)]
        if records:
            return records
    return []


def _expanded_authors(value: object) -> list[str]:
    authors: list[str] = []
    for author in _as_list(value):
        if isinstance(author, str):
            name = clean_text(author)
        elif isinstance(author, dict):
            name = _first_text(
                author.get("display_name"),
                author.get("displayName"),
                author.get("wos_standard"),
                author.get("wosStandard"),
                author.get("full_name"),
                author.get("fullName"),
            )
        else:
            name = None
        if name:
            authors.append(name)
    return list(dict.fromkeys(authors))


def _doi_from_identifiers(value: object) -> str | None:
    for identifier in _as_list(value):
        if isinstance(identifier, str):
            doi = normalize_doi(identifier)
        elif isinstance(identifier, dict):
            kind = str(identifier.get("type") or identifier.get("id_type") or "").lower()
            candidate = _first_text(
                identifier.get("value"),
                identifier.get("content"),
                identifier.get("id"),
            )
            doi = (
                normalize_doi(candidate)
                if kind == "doi" or (candidate is not None and candidate.lower().startswith("10."))
                else None
            )
        else:
            doi = None
        if doi:
            return doi
    return None


def _expanded_citation_count(value: object) -> int | None:
    counts: list[int] = []
    for citation in _as_list(value):
        if not isinstance(citation, dict):
            continue
        count = integer_or_none(citation.get("local_count") or citation.get("count"))
        if count is None:
            continue
        database = str(citation.get("coll_id") or citation.get("db") or "").upper()
        if database in {"WOS", "WOK"}:
            return count
        counts.append(count)
    return max(counts) if counts else None


def _first_text(*values: object) -> str | None:
    for value in values:
        texts = _text_values(value)
        if texts:
            return clean_text(" ".join(texts))
    return None


def _typed_text(value: object, expected_type: str) -> str | None:
    for item in _as_list(value):
        if isinstance(item, dict) and str(item.get("type") or "").lower() == expected_type.lower():
            text = _first_text(item.get("content"), item.get("value"), item.get("text"))
            if text:
                return text
    return None


def _text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _text_values(item)]
    if isinstance(value, dict):
        for key in ("content", "value", "text", "p"):
            if key in value:
                return _text_values(value[key])
    return []


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return [value] if value is not None else []


def _dig(payload: object, dotted_path: str) -> object:
    current = payload
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _year_value(value: object) -> int | None:
    if isinstance(value, str):
        match = re.match(r"^(\d{4})", value.strip())
        return int(match.group(1)) if match else None
    return integer_or_none(value)
