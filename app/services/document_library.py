"""Unified browsing of full articles and verified abstract-only records."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from contextlib import closing
from typing import Any, Literal

from app.database.sqlite import Database
from app.updates.models import normalize_doi

DocumentAvailability = Literal["all", "full_text", "abstract_only"]

_ALLOWED_STATUSES = {"unreviewed", "accepted", "review", "rejected"}
_STATUS_PRIORITY = {"accepted": 0, "review": 1, "unreviewed": 2, "rejected": 3}
_MAX_DOCUMENT_THEMES = 3
_CIDRE_THEME = "cidre"
_CIDRE_PATTERN = re.compile(
    r"\b(?:ciders?|cidres?|cidricoles?|cidriculture|cidreries?|cidrification|sidras?)\b"
)
_CIDRE_FTS_QUERY = (
    "cider OR ciders OR cidre OR cidres OR cidricole OR cidricoles OR "
    "cidriculture OR cidrerie OR cidreries OR cidrification OR sidra OR sidras"
)


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(character for character in text if not unicodedata.combining(character))


def _query_terms(query: str) -> list[tuple[str, str]]:
    raw_terms = [term.strip(",;:\"'") for term in " ".join(query.split()).split()]
    terms = [(term, _fold(term)) for term in raw_terms if term]
    if len(terms) > 50:
        raise ValueError("document query cannot exceed 50 terms")
    return terms


def _fts_query(term: str) -> str | None:
    tokens = re.findall(r"\w+", term, flags=re.UNICODE)
    if not tokens:
        return None
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


def _chunk_matches_by_term(
    database: Database,
    terms: Sequence[tuple[str, str]],
) -> dict[str, set[str]]:
    matches: dict[str, set[str]] = {folded: set() for _raw, folded in terms}
    if not terms:
        return matches
    with closing(database.connect()) as connection:
        for raw, folded in terms:
            fts_query = _fts_query(raw)
            if fts_query is None:
                continue
            rows = connection.execute(
                """
                SELECT DISTINCT article_id
                FROM chunks_fts
                WHERE chunks_fts MATCH ?
                """,
                (fts_query,),
            )
            matches[folded] = {str(row[0]) for row in rows}
    return matches


def _cidre_article_ids(database: Database) -> set[str]:
    """Return full articles whose indexed text explicitly mentions cider."""

    with closing(database.connect()) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT article_id
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            """,
            (_CIDRE_FTS_QUERY,),
        )
        return {str(row[0]) for row in rows}


def _json_authors(value: object) -> str:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return "[]"
    return json.dumps(parsed if isinstance(parsed, list) else [], ensure_ascii=False)


def _sources(value: object) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in str(value or "").split(",") if item.strip()))


def _load_rows(database: Database) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with closing(database.connect()) as connection:
        notice_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT r.*,
                    (
                        SELECT GROUP_CONCAT(DISTINCT s.source)
                        FROM bibliographic_record_sources AS s
                        WHERE s.record_id = r.id
                    ) AS sources,
                    (
                        SELECT MIN(s.first_seen_at)
                        FROM bibliographic_record_sources AS s
                        WHERE s.record_id = r.id
                    ) AS first_seen_at,
                    (
                        SELECT MAX(s.last_seen_at)
                        FROM bibliographic_record_sources AS s
                        WHERE s.record_id = r.id
                    ) AS last_seen_at
                FROM bibliographic_records AS r
                WHERE r.relevance_status = 'accepted'
                ORDER BY r.created_at, r.id
                """
            )
        ]
        article_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT a.*,
                    COUNT(c.id) AS chunk_count,
                    SUM(CASE WHEN c.embedding_status = 'indexed' THEN 1 ELSE 0 END)
                        AS indexed_chunk_count
                FROM articles AS a
                LEFT JOIN chunks AS c ON c.article_id = a.id
                GROUP BY a.id
                ORDER BY a.created_at, a.id
                """
            )
        ]
    return notice_rows, article_rows


def _verified_doi(value: object) -> str | None:
    """Accept only a complete, bare DOI that normalizes without correction."""

    if not isinstance(value, str):
        return None
    cleaned = value.strip().casefold()
    normalized = normalize_doi(cleaned)
    if normalized is None or normalized != cleaned:
        return None
    return normalized


def _abstract_document(record: dict[str, Any], article: dict[str, Any] | None) -> dict[str, Any]:
    has_full_text = article is not None
    sources = _sources(record.get("sources"))
    if article is not None and article.get("source"):
        sources = list(dict.fromkeys([*sources, str(article["source"])]))
    return {
        **record,
        "library_id": (f"article:{article['id']}" if has_full_text else f"abstract:{record['id']}"),
        "document_type": "full_text" if has_full_text else "abstract_only",
        "article_id": str(article["id"]) if article is not None else None,
        "pdf_available": has_full_text,
        "pdf_path": str(article["pdf_path"]) if article is not None else None,
        "validation_status": str(article["validation_status"]) if article is not None else None,
        "chunk_count": int(article["chunk_count"] or 0) if article is not None else 0,
        "indexed_chunk_count": (
            int(article["indexed_chunk_count"] or 0) if article is not None else 0
        ),
        "relevance_status": "accepted",
        "authors": _json_authors(record.get("authors") or (article or {}).get("authors")),
        "abstract": record.get("abstract") or (article or {}).get("abstract"),
        "sources": ",".join(sources) or None,
    }


def _article_document(article: dict[str, Any]) -> dict[str, Any]:
    doi = _verified_doi(article.get("doi"))
    chunk_count = int(article["chunk_count"] or 0)
    indexed_chunk_count = int(article["indexed_chunk_count"] or 0)
    return {
        "id": str(article["id"]),
        "library_id": f"article:{article['id']}",
        "canonical_key": f"doi:{doi}" if doi else f"article:{article['id']}",
        "doi": doi,
        "title": str(article["title"]),
        "abstract": article.get("abstract"),
        "authors": _json_authors(article.get("authors")),
        "journal": article.get("journal"),
        "work_type": article.get("work_type"),
        "publisher": article.get("publisher"),
        "publication_year": article.get("publication_year"),
        "citation_count": None,
        "url": f"https://doi.org/{doi}" if doi else None,
        "embedding_status": (
            "indexed" if chunk_count > 0 and indexed_chunk_count == chunk_count else "pending"
        ),
        "relevance_status": "accepted",
        "relevance_score": None,
        "relevance_reason": "Texte intégral présent dans le corpus scientifique.",
        "relevance_theme": None,
        "sources": str(article.get("source") or "local"),
        "first_seen_at": article.get("created_at"),
        "last_seen_at": article.get("indexed_at") or article.get("created_at"),
        "document_type": "full_text",
        "article_id": str(article["id"]),
        "pdf_available": True,
        "pdf_path": str(article["pdf_path"]),
        "validation_status": str(article["validation_status"]),
        "chunk_count": chunk_count,
        "indexed_chunk_count": indexed_chunk_count,
    }


def _documents(database: Database) -> list[dict[str, Any]]:
    abstract_records, articles = _load_rows(database)
    abstract_groups: dict[str, list[dict[str, Any]]] = {}
    for record in abstract_records:
        doi = _verified_doi(record.get("doi"))
        if doi is not None:
            abstract_groups.setdefault(doi, []).append(record)

    best_abstract_by_doi: dict[str, dict[str, Any]] = {}
    for doi, group in abstract_groups.items():
        group.sort(
            key=lambda record: (
                not bool(str(record.get("abstract") or "").strip()),
                -float(record.get("relevance_score") or 0.0),
                str(record["id"]),
            )
        )
        best_abstract_by_doi[doi] = group[0]

    documents: list[dict[str, Any]] = []
    full_text_dois: set[str] = set()
    for article in articles:
        doi = _verified_doi(article.get("doi"))
        if doi is not None:
            full_text_dois.add(doi)
        abstract_record = best_abstract_by_doi.get(doi) if doi is not None else None
        if abstract_record is None:
            documents.append(_article_document(article))
            continue
        document = _abstract_document(abstract_record, article)
        document["doi"] = doi
        document["canonical_key"] = f"doi:{doi}"
        document["url"] = f"https://doi.org/{doi}"
        group = abstract_groups[doi]
        combined_sources = list(
            dict.fromkeys(source for record in group for source in _sources(record.get("sources")))
        )
        if article.get("source"):
            combined_sources.append(str(article["source"]))
        document["sources"] = ",".join(dict.fromkeys(combined_sources)) or None
        documents.append(document)

    for doi, record in best_abstract_by_doi.items():
        if doi in full_text_dois or not str(record.get("abstract") or "").strip():
            continue
        document = _abstract_document(record, None)
        document["doi"] = doi
        document["canonical_key"] = f"doi:{doi}"
        document["url"] = f"https://doi.org/{doi}"
        document["sources"] = (
            ",".join(
                dict.fromkeys(
                    source
                    for candidate in abstract_groups[doi]
                    for source in _sources(candidate.get("sources"))
                )
            )
            or None
        )
        documents.append(document)
    return documents


def _metadata_haystack(document: dict[str, Any]) -> str:
    return _fold(
        " ".join(
            str(document.get(field) or "")
            for field in (
                "title",
                "abstract",
                "authors",
                "journal",
                "work_type",
                "publisher",
                "publication_year",
                "citation_count",
                "doi",
                "url",
                "relevance_theme",
                "sources",
                "pdf_path",
            )
        )
    )


def _document_themes(document: dict[str, Any], cidre_article_ids: set[str]) -> list[str]:
    """Return the primary theme plus bounded transversal documentary tags."""

    themes: list[str] = []
    if document.get("relevance_theme"):
        themes.append(str(document["relevance_theme"]))
    cidre_metadata = _fold(f"{document.get('title') or ''} {document.get('abstract') or ''}")
    article_id = str(document.get("article_id") or "")
    if _CIDRE_PATTERN.search(cidre_metadata) or article_id in cidre_article_ids:
        themes.append(_CIDRE_THEME)
    return list(dict.fromkeys(themes))[:_MAX_DOCUMENT_THEMES]


def browse_document_library(
    database: Database,
    *,
    query: str = "",
    statuses: Sequence[str] | None = None,
    theme: str | None = None,
    source: str | None = None,
    availability: DocumentAvailability = "all",
    has_abstract: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one document per verified DOI, with full articles taking priority."""

    if not 1 <= limit <= 200:
        raise ValueError("document browse limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("document browse offset cannot be negative")
    selected_statuses = list(dict.fromkeys(statuses or []))
    if not set(selected_statuses) <= _ALLOWED_STATUSES:
        raise ValueError("invalid document relevance status")
    if availability not in {"all", "full_text", "abstract_only"}:
        raise ValueError("invalid document availability")

    terms = _query_terms(query)
    chunk_matches = _chunk_matches_by_term(database, terms)
    cidre_article_ids = _cidre_article_ids(database)
    selected: list[dict[str, Any]] = []
    for document in _documents(database):
        document["themes"] = _document_themes(document, cidre_article_ids)
        article_id = document.get("article_id")
        haystack = _metadata_haystack(document)
        if any(
            folded not in haystack
            and (not article_id or str(article_id) not in chunk_matches.get(folded, set()))
            for _raw, folded in terms
        ):
            continue
        if selected_statuses and document["relevance_status"] not in selected_statuses:
            continue
        if theme and _fold(theme) not in {_fold(item) for item in document["themes"]}:
            continue
        if source and _fold(source) not in {_fold(item) for item in _sources(document["sources"])}:
            continue
        if availability == "full_text" and document["document_type"] != "full_text":
            continue
        if availability == "abstract_only" and document["document_type"] != "abstract_only":
            continue
        if has_abstract is True and not str(document.get("abstract") or "").strip():
            continue
        if has_abstract is False and str(document.get("abstract") or "").strip():
            continue
        selected.append(document)

    selected.sort(
        key=lambda document: (
            _STATUS_PRIORITY.get(str(document["relevance_status"]), 9),
            document["document_type"] != "full_text",
            -(int(document["publication_year"]) if document.get("publication_year") else 0),
            _fold(document["title"]),
            str(document["library_id"]),
        )
    )
    total = len(selected)
    return {
        "total": total,
        "records": selected[offset : offset + limit],
        "limit": limit,
        "offset": offset,
    }


def document_library_summary(database: Database) -> dict[str, Any]:
    documents = _documents(database)
    cidre_article_ids = _cidre_article_ids(database)
    for document in documents:
        document["themes"] = _document_themes(document, cidre_article_ids)
    full_texts = [document for document in documents if document["document_type"] == "full_text"]
    abstracts = [document for document in documents if document["document_type"] == "abstract_only"]
    indexed = [
        document
        for document in documents
        if document["embedding_status"] == "indexed"
        or int(document.get("indexed_chunk_count") or 0) > 0
    ]
    themes = sorted(
        {theme for document in documents for theme in document["themes"]} | {_CIDRE_THEME},
        key=_fold,
    )
    sources = sorted(
        {source for document in documents for source in _sources(document.get("sources"))},
        key=_fold,
    )
    return {
        "statistics": {
            "documents": len(documents),
            "full_texts": len(full_texts),
            "abstract_only": len(abstracts),
            "searchable": len(indexed),
        },
        "filters": {"themes": themes, "sources": sources},
    }
