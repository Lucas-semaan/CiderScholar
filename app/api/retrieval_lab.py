"""Read-only routes for explicit retrieval inspection."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_common_corpus_database, get_common_corpus_settings
from app.config import Settings
from app.database.sqlite import Database
from app.services.retrieval_lab import inspect_retrieval

router = APIRouter(prefix="/api/retrieval-lab", tags=["retrieval-lab"])


@router.get("/inspect")
def inspect(
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
    query: Annotated[str, Query(min_length=2, max_length=1_000)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Return bounded retrieval trace only after an explicit operator action."""

    return inspect_retrieval(settings, database, query=query, limit=limit)
