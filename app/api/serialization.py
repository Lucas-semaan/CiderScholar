"""Safe conversion helpers for SQLite rows exposed through JSON APIs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.database.sqlite import Database


def json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def serialize_row(row: Mapping[str, Any], *, json_fields: Sequence[str] = ()) -> dict[str, Any]:
    payload = dict(row)
    for field in json_fields:
        payload[field] = json_list(payload.get(field))
    return payload


def corpus_listing(database: Database, *, scope: str) -> dict[str, Any]:
    """Serialize the shared corpus listing contract for either local corpus scope."""

    articles = [serialize_row(row) for row in database.list_articles(limit=5000)]
    jobs = [serialize_row(row) for row in database.list_ingestion_jobs(limit=200)]
    return {
        "scope": scope,
        "articles": articles,
        "jobs": jobs,
        "summary": {
            "articles": len(articles),
            "chunks": sum(int(row["chunk_count"] or 0) for row in articles),
            "indexed_chunks": sum(int(row["indexed_chunk_count"] or 0) for row in articles),
            "failed_jobs": sum(row["state"] == "failed" for row in jobs),
            "ocr_jobs": sum(row["state"] == "ocr_required" for row in jobs),
        },
    }
