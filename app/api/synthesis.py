"""Persisted evidence and hierarchical synthesis routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_common_corpus_database, get_database, get_settings
from app.api.schemas import SynthesisRequest
from app.api.serialization import serialize_row
from app.config import Settings
from app.database.sqlite import Database
from app.jobs.contracts import JobPublic, LongSynthesisPayload
from app.jobs.repository import (
    LONG_SYNTHESIS_CONVERSATION_ID,
    EvaluationRunBusyError,
    JobRepository,
)
from app.services.workflows import load_completed_synthesis

router = APIRouter(prefix="/api/synthesis", tags=["synthesis"])


@router.get("")
def list_syntheses(
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    queries = [
        serialize_row(row, json_fields=("selected_article_ids",))
        for row in database.list_query_summaries(limit=1000)
    ]
    return {"queries": queries}


@router.get("/{query_id}")
def synthesis_detail(
    query_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_common_corpus_database)],
) -> dict[str, Any]:
    summary = next(
        (
            serialize_row(row, json_fields=("selected_article_ids",))
            for row in database.list_query_summaries(limit=1000)
            if str(row["id"]) == query_id
        ),
        None,
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Synthèse introuvable.")

    evidence_runs = []
    for row in database.evidence_run_rows_for_query(query_id):
        payload = serialize_row(
            row,
            json_fields=("topics", "contradictions", "missing_information", "selected_chunk_ids"),
        )
        evidence = database.load_article_evidence(query_id, str(row["article_id"]))
        payload["evidence"] = evidence.model_dump(mode="json") if evidence else None
        evidence_runs.append(payload)

    theme_plan = database.load_theme_plan(query_id)
    themes = []
    if theme_plan is not None:
        for assignment in theme_plan.themes:
            synthesis = database.load_theme_synthesis(query_id, assignment.theme_id)
            run = database.theme_synthesis_run(query_id, assignment.theme_id)
            themes.append(
                {
                    "assignment": assignment.model_dump(mode="json"),
                    "state": str(run["state"]) if run is not None else "pending",
                    "synthesis": synthesis.model_dump(mode="json") if synthesis else None,
                }
            )

    final_result = load_completed_synthesis(
        settings,
        database,
        query_id=query_id,
    )
    return {
        "summary": summary,
        "evidence_runs": evidence_runs,
        "theme_plan": theme_plan.model_dump(mode="json") if theme_plan else None,
        "themes": themes,
        "result": final_result.model_dump(mode="json") if final_result else None,
    }


@router.post(
    "/{query_id}/run",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_synthesis(
    query_id: str,
    payload: SynthesisRequest,
    scientific_database: Annotated[Database, Depends(get_common_corpus_database)],
    application_database: Annotated[Database, Depends(get_database)],
) -> JobPublic:
    known_query = any(
        str(row["id"]) == query_id for row in scientific_database.list_query_summaries(limit=1000)
    )
    if not known_query:
        raise HTTPException(status_code=404, detail="Synthèse introuvable.")
    if not scientific_database.evidence_records_for_query(query_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Impossible de synthétiser cette analyse : aucune preuve factuelle "
                "validée n’est disponible ou la traçabilité des sources est incomplète."
            ),
        )
    try:
        job = JobRepository(application_database.path).enqueue_long_synthesis(
            LongSynthesisPayload(
                query_id=query_id,
                resume=payload.resume,
                conversation_id=LONG_SYNTHESIS_CONVERSATION_ID,
                client_request_id=payload.client_request_id,
            )
        )
    except EvaluationRunBusyError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evaluation_run_busy",
                "message": "Attendez la fin de la cellule d'évaluation active.",
            },
        ) from error
    return job.to_public()
