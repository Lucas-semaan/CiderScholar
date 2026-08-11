"""Conservative metadata matching for documents already present in the corpus."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from app.updates.models import BibliographicRecord, normalize_doi

RecordKind = Literal["article", "bibliographic_record"]

_EMPTY_AUTHORS = {"", "[]", "null"}
_GENERIC_TITLE_KEYS = {
    "advancein",
    "guidesguides",
    "nojobname",
    "untitled",
}
_FILE_SUFFIX = re.compile(r"\.(?:pdf|xml|txt)$", re.IGNORECASE)
_LEADING_FILE_METADATA = re.compile(
    r"^\s*(?P<author>[^-]{2,80}?)\s+-\s+(?P<year>(?:19|20)\d{2}|s[.]?d[.]?)\s+-\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MetadataTarget:
    kind: RecordKind
    record_id: str
    title: str
    doi: str | None
    authors: tuple[str, ...]
    journal: str | None
    work_type: str | None
    publisher: str | None
    publication_year: int | None
    pdf_path: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class MatchAssessment:
    status: Literal["accepted", "review", "rejected"]
    method: str
    title_similarity: float
    reason: str


@dataclass(frozen=True)
class MetadataUpdate:
    kind: RecordKind
    record_id: str
    provider: str
    provider_id: str
    source_url: str | None
    method: str
    confidence: float
    fields: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_authors(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(author).strip() for author in value if str(author).strip())
    text = str(value or "").strip()
    if text.casefold() in _EMPTY_AUTHORS:
        return ()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        return tuple(str(author).strip() for author in parsed if str(author).strip())
    return (text,)


def target_from_row(kind: RecordKind, row: dict[str, Any]) -> MetadataTarget:
    return MetadataTarget(
        kind=kind,
        record_id=str(row["id"]),
        title=str(row.get("title") or "").strip(),
        doi=normalize_doi(row.get("doi")),
        authors=parse_authors(row.get("authors")),
        journal=_optional_text(row.get("journal")),
        work_type=_optional_text(row.get("work_type")),
        publisher=_optional_text(row.get("publisher")),
        publication_year=_year(row.get("publication_year")),
        pdf_path=_optional_text(row.get("pdf_path")),
        source=_optional_text(row.get("source")),
    )


def normalized_title(value: object) -> str:
    text = html.unescape(str(value or "")).casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def title_similarity(left: object, right: object) -> float:
    first = normalized_title(left)
    second = normalized_title(right)
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second, autojunk=False).ratio()


def title_variants(target: MetadataTarget) -> tuple[str, ...]:
    variants: list[str] = []
    if _usable_title(target.title):
        variants.append(target.title)
    if target.pdf_path:
        stem = _FILE_SUFFIX.sub("", Path(target.pdf_path).name)
        without_prefix = _LEADING_FILE_METADATA.sub("", stem).strip(" -_")
        if _usable_title(without_prefix):
            variants.append(without_prefix)
    return tuple(dict.fromkeys(variants))


def preferred_search_title(target: MetadataTarget) -> str | None:
    variants = title_variants(target)
    return max(variants, key=len) if variants else None


def assess_title_candidate(
    target: MetadataTarget,
    candidate: BibliographicRecord,
    *,
    minimum_similarity: float = 0.96,
) -> MatchAssessment:
    variants = title_variants(target)
    similarity = max((title_similarity(title, candidate.title) for title in variants), default=0.0)
    if candidate.doi is None:
        return MatchAssessment("rejected", "title_search", similarity, "candidate has no DOI")
    if similarity < minimum_similarity:
        return MatchAssessment(
            "rejected",
            "title_search",
            similarity,
            f"title similarity {similarity:.3f} is below {minimum_similarity:.3f}",
        )
    if not _years_compatible(target.publication_year, candidate.publication_year):
        return MatchAssessment(
            "review",
            "title_search",
            similarity,
            "publication years differ by more than one year",
        )
    if (
        target.authors
        and candidate.authors
        and not _authors_overlap(target.authors, candidate.authors)
    ):
        return MatchAssessment(
            "review",
            "title_search",
            similarity,
            "existing and candidate authors do not overlap",
        )
    return MatchAssessment(
        "accepted",
        "title_search",
        similarity,
        "title and available identity fields agree",
    )


def assess_cross_validated_candidate(
    target: MetadataTarget,
    crossref: BibliographicRecord,
    openalex: BibliographicRecord | None,
) -> MatchAssessment:
    initial = assess_title_candidate(target, crossref)
    if initial.status != "accepted":
        return initial
    if openalex is None or openalex.doi != crossref.doi:
        return MatchAssessment(
            "review",
            "provider_title_openalex_doi",
            initial.title_similarity,
            "Crossref DOI was not independently resolved by OpenAlex",
        )
    independent_similarity = title_similarity(crossref.title, openalex.title)
    if independent_similarity < 0.94:
        return MatchAssessment(
            "review",
            "provider_title_openalex_doi",
            initial.title_similarity,
            f"provider titles disagree ({independent_similarity:.3f})",
        )
    if not _years_compatible(crossref.publication_year, openalex.publication_year):
        return MatchAssessment(
            "review",
            "provider_title_openalex_doi",
            initial.title_similarity,
            "Crossref and OpenAlex years differ by more than one year",
        )
    return MatchAssessment(
        "accepted",
        "provider_title_openalex_doi",
        min(initial.title_similarity, independent_similarity),
        "Crossref title match and OpenAlex DOI resolution agree",
    )


def build_update(
    target: MetadataTarget,
    primary: BibliographicRecord,
    *,
    method: str,
    confidence: float,
    secondary: BibliographicRecord | None = None,
) -> MetadataUpdate | None:
    records = [primary, *([secondary] if secondary is not None else [])]
    fields: dict[str, Any] = {}
    if target.doi is None:
        doi = next((record.doi for record in records if record.doi), None)
        if doi:
            fields["doi"] = doi
    if not target.authors:
        authors = next((record.authors for record in records if record.authors), [])
        if authors:
            fields["authors"] = list(dict.fromkeys(authors))
    if target.publication_year is None:
        year = next(
            (record.publication_year for record in records if record.publication_year is not None),
            None,
        )
        if year is not None:
            fields["publication_year"] = year
    if target.journal is None:
        journal = next((record.journal for record in records if record.journal), None)
        if journal:
            fields["journal"] = journal
    if target.work_type is None:
        work_type = next((record.work_type for record in records if record.work_type), None)
        if work_type:
            fields["work_type"] = work_type
    if target.publisher is None:
        publisher = next((record.publisher for record in records if record.publisher), None)
        if publisher:
            fields["publisher"] = publisher
    if not fields:
        return None
    return MetadataUpdate(
        kind=target.kind,
        record_id=target.record_id,
        provider=primary.source,
        provider_id=primary.source_id,
        source_url=primary.url,
        method=method,
        confidence=max(0.0, min(confidence, 1.0)),
        fields=fields,
    )


def merge_provider_records(
    primary: BibliographicRecord,
    secondary: BibliographicRecord | None,
) -> BibliographicRecord:
    if secondary is None:
        return primary
    if primary.doi and secondary.doi and primary.doi != secondary.doi:
        raise ValueError("cannot merge metadata for different DOI values")
    return BibliographicRecord(
        source=f"{primary.source} + {secondary.source}",
        source_id=f"{primary.source_id}|{secondary.source_id}",
        title=primary.title,
        authors=list(dict.fromkeys([*primary.authors, *secondary.authors])),
        abstract=primary.abstract or secondary.abstract,
        journal=primary.journal or secondary.journal,
        work_type=primary.work_type or secondary.work_type,
        publisher=primary.publisher or secondary.publisher,
        publication_year=primary.publication_year or secondary.publication_year,
        doi=primary.doi or secondary.doi,
        citation_count=primary.citation_count or secondary.citation_count,
        url=primary.url or secondary.url,
        relevance_score=primary.relevance_score,
    )


def _usable_title(value: str) -> bool:
    key = normalized_title(value).replace(" ", "")
    if len(key) < 12 or key in _GENERIC_TITLE_KEYS:
        return False
    return not key.startswith(("doi10", "issn", "pii", "ctypeset"))


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _year(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if 1600 <= parsed <= 2200 else None


def _years_compatible(left: int | None, right: int | None) -> bool:
    return left is None or right is None or abs(left - right) <= 1


def _author_keys(authors: tuple[str, ...] | list[str]) -> set[str]:
    keys: set[str] = set()
    for author in authors:
        tokens = normalized_title(author).split()
        keys.update(token for token in tokens if len(token) >= 3)
    return keys


def _authors_overlap(left: tuple[str, ...] | list[str], right: tuple[str, ...] | list[str]) -> bool:
    return bool(_author_keys(left) & _author_keys(right))
