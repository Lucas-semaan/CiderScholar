"""Public durable-job routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_database
from app.api.schemas import JobRetryRequest
from app.database.sqlite import Database
from app.jobs.contracts import JobPublic, JobState
from app.jobs.repository import (
    ActiveJobLimitError,
    EvaluationConversationIsolationError,
    EvaluationQuestionAlreadySubmittedError,
    EvaluationRunBusyError,
    JobRepository,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobPublic)
def get_job(
    job_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> JobPublic:
    job = JobRepository(database.path).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Travail introuvable.")
    return job.to_public()


@router.post("/{job_id}/cancel", response_model=JobPublic)
def cancel_job(
    job_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> JobPublic:
    repository = JobRepository(database.path)
    job = repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Travail introuvable.")
    if job.state is JobState.QUEUED:
        cancelled = repository.cancel_queued(job_id)
    elif job.state is JobState.RUNNING:
        cancelled = repository.request_cancellation(job_id)
    else:
        raise HTTPException(status_code=409, detail="Ce travail ne peut plus être annulé.")
    if cancelled is None:
        raise HTTPException(status_code=409, detail="L'état du travail a changé.")
    return cancelled.to_public()


@router.post(
    "/{job_id}/retry",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job(
    job_id: UUID,
    payload: JobRetryRequest,
    database: Annotated[Database, Depends(get_database)],
) -> JobPublic:
    repository = JobRepository(database.path)
    job = repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Travail introuvable.")
    if job.state is not JobState.FAILED:
        raise HTTPException(status_code=409, detail="Seul un travail en échec peut être relancé.")
    try:
        retried = repository.retry_failed(
            job_id,
            client_request_id=payload.client_request_id,
        )
    except ActiveJobLimitError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_job_limit",
                "message": "Attendez la fin d'un travail actif avant de relancer.",
                "limit": error.limit,
            },
        ) from error
    except EvaluationRunBusyError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evaluation_run_busy",
                "message": "Attendez le résultat terminal du job durable actif.",
            },
        ) from error
    except (
        EvaluationConversationIsolationError,
        EvaluationQuestionAlreadySubmittedError,
    ) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evaluation_retry_integrity",
                "message": "La cellule d'évaluation ne peut pas être relancée en l'état.",
            },
        ) from error
    if retried is None:
        raise HTTPException(status_code=409, detail="L'état du travail a changé.")
    return retried.to_public()
