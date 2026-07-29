"""Local pilot defect intake without chat, job, or document identifiers."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_database
from app.database.sqlite import Database
from app.pilot_feedback import PilotDefect, PilotDefectCreate, PilotFeedbackRepository

router = APIRouter(prefix="/api/pilot-feedback", tags=["pilot-feedback"])


@router.post("", response_model=PilotDefect, status_code=status.HTTP_201_CREATED)
def submit_pilot_defect(
    payload: PilotDefectCreate,
    database: Annotated[Database, Depends(get_database)],
) -> PilotDefect:
    return PilotFeedbackRepository(database).create(payload)


@router.get("", response_model=list[PilotDefect])
def list_pilot_defects(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PilotDefect]:
    return PilotFeedbackRepository(database).list_recent(limit=limit)
