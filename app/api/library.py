"""Browsable local bibliographic database routes."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.dependencies import get_common_corpus_database, get_common_corpus_settings
from app.api.schemas import LibraryReviewDecisionRequest
from app.config import Settings
from app.database.sqlite import Database
from app.services.document_library import browse_document_library, document_library_summary
from app.services.library_review import decide_bibliographic_review
from app.updates.harvest import BibliographicReviewConflictError

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/summary")
def library_summary(
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    return document_library_summary(database)


@router.get("/records")
def library_records(
    database: Annotated[Database, Depends(get_common_corpus_database)],
    query: Annotated[str, Query(max_length=500)] = "",
    statuses: Annotated[str, Query(max_length=200)] = "",
    theme: Annotated[str | None, Query(max_length=100)] = None,
    source: Annotated[str | None, Query(max_length=200)] = None,
    availability: Literal["all", "full_text", "abstract_only"] = "all",
    abstract: Literal["all", "with", "without"] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    selected_statuses = [status.strip() for status in statuses.split(",") if status.strip()]
    has_abstract = {"all": None, "with": True, "without": False}[abstract]
    return browse_document_library(
        database,
        query=query,
        statuses=selected_statuses,
        theme=theme,
        source=source,
        availability=availability,
        has_abstract=has_abstract,
        limit=limit,
        offset=offset,
    )


@router.post("/records/{record_id}/decision")
def library_review_decision(
    payload: LibraryReviewDecisionRequest,
    record_id: Annotated[str, Path(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(get_common_corpus_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    try:
        return decide_bibliographic_review(
            settings,
            database,
            record_id=record_id,
            decision=payload.decision,
        )
    except BibliographicReviewConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
