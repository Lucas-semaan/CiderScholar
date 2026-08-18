"""Official OpenCitations graph and metadata access for bounded DOI discovery."""

from __future__ import annotations

import os
import re
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from app.updates.base import BibliographicApiError, OfficialBibliographicClient
from app.updates.models import (
    DOI_PATTERN,
    BibliographicRecord,
    clean_text,
    integer_or_none,
    normalize_doi,
)

OpenCitationRelationKind = Literal["citation", "reference"]


class OpenCitationRelation(BaseModel):
    """One normalized DOI-to-DOI edge returned by the OpenCitations Index."""

    model_config = ConfigDict(extra="forbid")

    seed_doi: str
    relation: OpenCitationRelationKind
    related_doi: str
    oci: str | None = None
    creation: str | None = None


class OpenCitationsClient(OfficialBibliographicClient):
    source_id = "opencitations"
    source_label = "OpenCitations"
    minimum_request_delay_seconds = 0.35

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "CiderScholar/0.2 (local scientific corpus enrichment)"}
        token = os.environ.get(self.config.opencitations_api_key_env, "").strip()
        if token:
            headers["authorization"] = token
        return headers

    def relations(
        self,
        doi: str,
        relation: OpenCitationRelationKind,
    ) -> list[OpenCitationRelation]:
        seed_doi = normalize_doi(doi)
        if seed_doi is None:
            raise ValueError("OpenCitations seed DOI is invalid")
        operation = "citations" if relation == "citation" else "references"
        rows = self._get_list(
            f"{self.config.opencitations_index_base_url}/{operation}/"
            f"{quote(f'doi:{seed_doi}', safe=':/')}",
        )
        field = "citing" if relation == "citation" else "cited"
        edges: list[OpenCitationRelation] = []
        seen: set[str] = set()
        for row in rows:
            for related_doi in _dois(row.get(field)):
                if related_doi == seed_doi or related_doi in seen:
                    continue
                seen.add(related_doi)
                edges.append(
                    OpenCitationRelation(
                        seed_doi=seed_doi,
                        relation=relation,
                        related_doi=related_doi,
                        oci=clean_text(row.get("oci")),
                        creation=clean_text(row.get("creation")),
                    )
                )
        return edges

    def lookup_dois(self, dois: list[str]) -> list[BibliographicRecord]:
        normalized = list(
            dict.fromkeys(doi for value in dois if (doi := normalize_doi(value)) is not None)
        )
        if len(normalized) > 50:
            raise ValueError("OpenCitations metadata lookup accepts at most 50 DOI values")
        if not normalized:
            return []
        identifiers = "__".join(
            quote(f"doi:{doi}", safe=":/().-").replace("_", "%5F") for doi in normalized
        )
        rows = self._get_list(f"{self.config.opencitations_meta_base_url}/metadata/{identifiers}")
        requested = set(normalized)
        records: list[BibliographicRecord] = []
        for row in rows:
            doi = next((value for value in _dois(row.get("id")) if value in requested), None)
            title = clean_text(row.get("title"))
            if doi is None or title is None:
                continue
            records.append(
                BibliographicRecord(
                    source="OpenCitations Meta",
                    source_id=clean_text(row.get("id")) or doi,
                    title=title,
                    authors=_people(row.get("author")),
                    journal=_without_identifiers(row.get("venue")),
                    work_type=clean_text(row.get("type")),
                    publisher=_without_identifiers(row.get("publisher")),
                    publication_year=_year(row.get("pub_date")),
                    doi=doi,
                    url=f"https://doi.org/{doi}",
                )
            )
        return records

    def _get_list(self, url: str) -> list[dict[str, Any]]:
        response = self._get_response(url, params={}, headers=self._headers())
        try:
            payload = response.json()
        except ValueError as exc:
            raise BibliographicApiError("OpenCitations returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise BibliographicApiError("OpenCitations returned an unexpected JSON structure")
        return [item for item in payload if isinstance(item, dict)]


def _dois(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    values: list[str] = []
    for match in DOI_PATTERN.finditer(value):
        doi = normalize_doi(match.group(0))
        if doi and doi not in values:
            values.append(doi)
    return values


def _people(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    authors: list[str] = []
    for item in value.split(";"):
        name = re.sub(r"\s*\[[^]]*]\s*$", "", item).strip()
        if name and name not in authors:
            authors.append(name)
    return authors


def _without_identifiers(value: object) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return re.sub(r"\s*\[[^]]*]\s*$", "", cleaned).strip() or None


def _year(value: object) -> int | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    year = integer_or_none(cleaned[:4])
    return year if year is not None and 1600 <= year <= 2200 else None
