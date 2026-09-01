"""Unified browsing of full articles, abstract-only records, and acquisition leads."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from app.database.sqlite import Database
from app.updates.models import normalize_doi

DocumentAvailability = Literal["all", "full_text", "abstract_only", "metadata_only"]

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
_DOCUMENT_CACHE_LOCK = Lock()


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
                WITH source_summary AS (
                    SELECT record_id,
                        GROUP_CONCAT(DISTINCT source) AS sources,
                        MIN(first_seen_at) AS first_seen_at,
                        MAX(last_seen_at) AS last_seen_at
                    FROM bibliographic_record_sources
                    GROUP BY record_id
                ), ranked_assets AS (
                    SELECT record_id, article_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY record_id
                            ORDER BY source_priority, observed_at DESC, article_id
                        ) AS position
                    FROM (
                        SELECT record_id, article_id, updated_at AS observed_at,
                            0 AS source_priority
                        FROM full_text_assets
                        WHERE article_id IS NOT NULL
                        UNION ALL
                        SELECT record_id, article_id, created_at AS observed_at,
                            1 AS source_priority
                        FROM publisher_full_text_assets
                        WHERE article_id IS NOT NULL
                    )
                ), linked_assets AS (
                    SELECT record_id, article_id
                    FROM ranked_assets
                    WHERE position = 1
                )
                SELECT r.*,
                    source_summary.sources,
                    source_summary.first_seen_at,
                    source_summary.last_seen_at,
                    linked_assets.article_id AS linked_article_id
                FROM bibliographic_records AS r
                LEFT JOIN source_summary ON source_summary.record_id = r.id
                LEFT JOIN linked_assets ON linked_assets.record_id = r.id
                ORDER BY r.created_at, r.id
                """
            )
        ]
        article_rows = [
            dict(row)
            for row in connection.execute("SELECT * FROM articles ORDER BY created_at, id")
        ]
        chunk_counts = {
            str(row["article_id"]): int(row["chunk_count"] or 0)
            for row in connection.execute(
                """
                SELECT article_id, COUNT(*) AS chunk_count
                FROM chunks
                GROUP BY article_id
                """
            )
        }
        incomplete_article_ids = [
            str(article["id"])
            for article in article_rows
            if article.get("validation_status") != "indexed" or article.get("indexed_at") is None
        ]
        indexed_chunk_counts: dict[str, int] = {}
        for start in range(0, len(incomplete_article_ids), 900):
            batch = incomplete_article_ids[start : start + 900]
            placeholders = ",".join("?" for _ in batch)
            indexed_chunk_counts.update(
                {
                    str(row["article_id"]): int(row["indexed_chunk_count"] or 0)
                    for row in connection.execute(
                        f"""
                        SELECT article_id, COUNT(*) AS indexed_chunk_count
                        FROM chunks INDEXED BY idx_chunks_article
                        WHERE article_id IN ({placeholders})
                          AND embedding_status = 'indexed'
                        GROUP BY article_id
                        """,
                        batch,
                    )
                }
            )
        for article in article_rows:
            article_id = str(article["id"])
            chunk_count = chunk_counts.get(article_id, 0)
            indexed_chunk_count = (
                chunk_count
                if article.get("validation_status") == "indexed"
                and article.get("indexed_at") is not None
                else indexed_chunk_counts.get(article_id, 0)
            )
            article["chunk_count"] = chunk_count
            article["indexed_chunk_count"] = indexed_chunk_count
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


def _metadata_document(record: dict[str, Any]) -> dict[str, Any]:
    """Expose one content-free lead without promoting it to scientific evidence."""

    doi = _verified_doi(record.get("doi"))
    return {
        **record,
        "library_id": f"notice:{record['id']}",
        "document_type": "metadata_only",
        "article_id": None,
        "pdf_available": False,
        "pdf_path": None,
        "validation_status": None,
        "chunk_count": 0,
        "indexed_chunk_count": 0,
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else record.get("url"),
        "authors": _json_authors(record.get("authors")),
        "abstract": None,
        "sources": ",".join(_sources(record.get("sources"))) or None,
    }


def _preferred_record(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        records,
        key=lambda record: (
            not bool(str(record.get("abstract") or "").strip()),
            _STATUS_PRIORITY.get(str(record.get("relevance_status")), 9),
            -float(record.get("relevance_score") or 0.0),
            str(record["id"]),
        ),
    )[0]


def _build_documents(database: Database) -> list[dict[str, Any]]:
    """Classify availability from persisted content, independently of relevance."""

    notice_records, articles = _load_rows(database)
    groups: dict[str, list[dict[str, Any]]] = {}
    identity_by_record_id: dict[str, str] = {}
    records_by_article_id: dict[str, list[dict[str, Any]]] = {}
    for record in notice_records:
        doi = _verified_doi(record.get("doi"))
        identity = f"doi:{doi}" if doi is not None else f"record:{record['id']}"
        groups.setdefault(identity, []).append(record)
        identity_by_record_id[str(record["id"])] = identity
        linked_article_id = str(record.get("linked_article_id") or "")
        if linked_article_id:
            records_by_article_id.setdefault(linked_article_id, []).append(record)

    documents: list[dict[str, Any]] = []
    consumed_identities: set[str] = set()
    for article in articles:
        doi = _verified_doi(article.get("doi"))
        doi_identity = f"doi:{doi}" if doi is not None else None
        linked_records = records_by_article_id.get(str(article["id"]), [])
        matching_records = list(linked_records)
        if doi_identity is not None:
            matching_records.extend(groups.get(doi_identity, []))
        matching_records = list({str(record["id"]): record for record in matching_records}.values())
        notice_record = _preferred_record(matching_records) if matching_records else None
        if notice_record is None:
            documents.append(_article_document(article))
            continue
        consumed_identities.update(
            identity_by_record_id[str(record["id"])] for record in matching_records
        )
        document = _abstract_document(notice_record, article)
        if doi is not None:
            document["doi"] = doi
            document["canonical_key"] = f"doi:{doi}"
            document["url"] = f"https://doi.org/{doi}"
        combined_sources = list(
            dict.fromkeys(
                source for record in matching_records for source in _sources(record.get("sources"))
            )
        )
        if article.get("source"):
            combined_sources.append(str(article["source"]))
        document["sources"] = ",".join(dict.fromkeys(combined_sources)) or None
        documents.append(document)

    for identity, group in groups.items():
        if identity in consumed_identities:
            continue
        records_with_abstract = [
            record for record in group if str(record.get("abstract") or "").strip()
        ]
        if not records_with_abstract:
            documents.append(_metadata_document(_preferred_record(group)))
            continue
        record = _preferred_record(records_with_abstract)
        document = _abstract_document(record, None)
        doi = _verified_doi(record.get("doi"))
        document["doi"] = doi
        if doi is not None:
            document["canonical_key"] = f"doi:{doi}"
            document["url"] = f"https://doi.org/{doi}"
        document["sources"] = (
            ",".join(
                dict.fromkeys(
                    source for candidate in group for source in _sources(candidate.get("sources"))
                )
            )
            or None
        )
        documents.append(document)
    return documents


def _database_signature(path: Path) -> tuple[int, int, int, int]:
    database_stat = path.stat()
    wal_path = Path(f"{path}-wal")
    if wal_path.is_file():
        wal_stat = wal_path.stat()
        return (
            database_stat.st_mtime_ns,
            database_stat.st_size,
            wal_stat.st_mtime_ns,
            wal_stat.st_size,
        )
    return (database_stat.st_mtime_ns, database_stat.st_size, -1, -1)


@lru_cache(maxsize=2)
def _cached_document_snapshot(
    database_path: str,
    _signature: tuple[int, int, int, int],
) -> tuple[dict[str, Any], ...]:
    return tuple(_build_documents(Database(Path(database_path))))


def _documents(database: Database) -> list[dict[str, Any]]:
    """Return an immutable-by-convention snapshot shared by simultaneous library reads."""

    database_path = database.path.resolve()
    signature = _database_signature(database_path)
    with _DOCUMENT_CACHE_LOCK:
        snapshot = _cached_document_snapshot(str(database_path), signature)
    return [dict(document) for document in snapshot]


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
    if availability not in {"all", "full_text", "abstract_only", "metadata_only"}:
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
        if availability == "all" and document["document_type"] == "metadata_only":
            continue
        if availability != "all" and document["document_type"] != availability:
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
    acquisition_notices = [
        document for document in documents if document["document_type"] == "metadata_only"
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
            "documents": len(full_texts) + len(abstracts),
            "full_texts": len(full_texts),
            "abstract_only": len(abstracts),
            "acquisition_notices": len(acquisition_notices),
            "accepted_without_content": sum(
                document["relevance_status"] == "accepted" for document in acquisition_notices
            ),
            "review_without_content": sum(
                document["relevance_status"] == "review" for document in acquisition_notices
            ),
        },
        "filters": {"themes": themes, "sources": sources},
    }
