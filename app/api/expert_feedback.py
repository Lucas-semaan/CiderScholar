"""Private, non-generative expert-correction submission endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.admin_maintenance import _admin_profile
from app.api.dependencies import get_database, get_settings
from app.config import Settings
from app.database.sqlite import Database
from app.evaluation.expert_memory import (
    ExpertEvaluationError,
    ExpertEvaluationEvidenceError,
    enqueue_evaluation_comparison_job,
    enqueue_evaluation_orchestrator_job,
    get_evaluation,
)
from app.evaluation.expert_pilot import (
    ExpertPilotAudit,
    ExpertPilotError,
    ExpertPilotObservation,
    attest_expert_pilot,
)
from app.evaluation.expert_pilot_store import (
    ExpertPilotStoreError,
    attest_pilot,
    audit_pilot,
    create_pilot_plan,
    get_pilot,
    record_pilot_observation,
)
from app.expert_feedback.candidates import ExpertCandidateCompiler, ExpertCandidateError
from app.expert_feedback.diagnosis import list_diagnoses
from app.expert_feedback.models import (
    ExpertActivationRequest,
    ExpertCandidateJobRequest,
    ExpertCorrectionCreate,
    ExpertCorrectionUpdate,
    ExpertDiagnosisJobRequest,
    ExpertDistributionActivationRequest,
    ExpertDistributionApprovalRequest,
    ExpertDistributionRollbackRequest,
    ExpertEvaluationJobRequest,
    ExpertEvaluationOrchestrationRequest,
    ExpertPilotAttestationRequest,
    ExpertPilotObservationCreate,
    ExpertPilotPlanRequest,
    ExpertReviewCreate,
    ExpertRollbackRequest,
)
from app.expert_feedback.repository import (
    ExpertCorrectionConflictError,
    ExpertCorrectionRepository,
    ExpertCorrectionValidationError,
)
from app.expert_feedback.review import (
    ExpertReviewConflictError,
    ExpertReviewError,
    activate_candidate,
    create_review,
    rollback_active_candidate,
)
from app.jobs.contracts import ExpertImprovementPayload, JobPublic
from app.jobs.repository import ExpertImprovementConflictError, JobRepository
from app.knowledge.package import (
    ExpertMemoryPackageError,
    activate_expert_memory_distribution,
    approve_expert_memory_distribution,
    get_expert_memory_distribution,
    list_expert_memory_distributions,
    list_expert_memory_releases,
    propose_expert_memory_distribution,
    rollback_expert_memory_distribution,
)

router = APIRouter(prefix="/api/chatbot", tags=["expert-memory"])
memory_router = APIRouter(prefix="/api/expert-memory", tags=["expert-memory"])


@router.post(
    "/messages/{message_id}/expert-corrections",
    status_code=status.HTTP_201_CREATED,
)
def submit_expert_correction(
    message_id: UUID,
    payload: ExpertCorrectionCreate,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        result, created = ExpertCorrectionRepository(database).submit(message_id, payload)
    except ExpertCorrectionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "expert_correction_idempotency_conflict", "message": str(error)},
        ) from error
    except ExpertCorrectionValidationError as error:
        code = "expert_correction_invalid_reference"
        if "does not exist" in str(error):
            code = "message_not_found"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if code == "message_not_found" else 422,
            detail={"code": code, "message": str(error)},
        ) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@memory_router.get("/corrections")
def list_expert_corrections(
    database: Annotated[Database, Depends(get_database)],
    correction_status: Annotated[str | None, Query(alias="status", max_length=40)] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    try:
        if correction_status is not None and correction_status not in {
            "submitted",
            "diagnosis_incomplete",
            "diagnosed",
            "needs_expert",
            "candidate_ready",
            "resolved",
            "rejected",
            "withdrawn",
        }:
            raise ValueError("correction status is invalid")
        corrections = ExpertCorrectionRepository(database).list(
            status=correction_status,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_cursor", "message": str(error)}
        ) from error
    next_cursor = None
    if len(corrections) == limit:
        last = corrections[-1]
        next_cursor = f"{last['created_at']}|{last['id']}"
    return {"corrections": corrections, "next_cursor": next_cursor}


@memory_router.get("/corrections/{correction_id}")
def get_expert_correction(
    correction_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    correction = ExpertCorrectionRepository(database).get(correction_id)
    if correction is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "correction_not_found", "message": "Correction introuvable."},
        )
    correction["diagnoses"] = list_diagnoses(database, correction_id)
    return correction


@memory_router.patch("/corrections/{correction_id}")
def update_expert_correction(
    correction_id: UUID,
    payload: ExpertCorrectionUpdate,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return ExpertCorrectionRepository(database).update(correction_id, payload)
    except ExpertCorrectionConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "correction_revision_conflict", "message": str(error)},
        ) from error
    except ExpertCorrectionValidationError as error:
        code = "correction_not_found" if "does not exist" in str(error) else "invalid_correction"
        raise HTTPException(
            status_code=404 if code == "correction_not_found" else 422,
            detail={"code": code, "message": str(error)},
        ) from error


@memory_router.post(
    "/corrections/{correction_id}/diagnosis-jobs",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_expert_diagnosis(
    correction_id: UUID,
    payload: ExpertDiagnosisJobRequest,
    database: Annotated[Database, Depends(get_database)],
) -> JobPublic:
    correction = ExpertCorrectionRepository(database).get(correction_id)
    if correction is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "correction_not_found", "message": "Correction introuvable."},
        )
    if correction["revision"] != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "correction_revision_conflict",
                "message": "La correction a changé depuis la demande de diagnostic.",
            },
        )
    if correction["status"] == "withdrawn":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "correction_withdrawn",
                "message": "Une correction retirée ne peut pas être diagnostiquée.",
            },
        )
    with database.connect() as connection:
        message = connection.execute(
            "SELECT conversation_id FROM chat_messages WHERE id = ?",
            (str(correction["message_id"]),),
        ).fetchone()
    if message is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "message_not_found", "message": "Message assistant introuvable."},
        )
    job_payload = ExpertImprovementPayload(
        correction_id=correction_id,
        expected_revision=payload.expected_revision,
        max_llm_requests=payload.max_llm_requests,
        conversation_id=UUID(str(message["conversation_id"])),
        client_request_id=payload.client_request_id,
    )
    try:
        job = JobRepository(database.path).enqueue_expert_improvement(
            job_payload,
            user_message_id=UUID(str(correction["message_id"])),
        )
    except ExpertImprovementConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_improvement_idempotency_conflict", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_expert_improvement", "message": str(error)},
        ) from error
    return job.to_public()


@memory_router.post(
    "/diagnoses/{diagnosis_id}/candidate-jobs",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_admin_profile)],
)
def enqueue_expert_candidate(
    diagnosis_id: UUID,
    payload: ExpertCandidateJobRequest,
    database: Annotated[Database, Depends(get_database)],
) -> JobPublic:
    with database.connect() as connection:
        diagnosis = connection.execute(
            "SELECT correction_id FROM expert_diagnoses WHERE id = ?",
            (str(diagnosis_id),),
        ).fetchone()
    if diagnosis is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "diagnosis_not_found", "message": "Diagnostic introuvable."},
        )
    correction = ExpertCorrectionRepository(database).get(UUID(str(diagnosis["correction_id"])))
    if correction is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "correction_not_found", "message": "Correction introuvable."},
        )
    with database.connect() as connection:
        message = connection.execute(
            "SELECT conversation_id FROM chat_messages WHERE id = ?",
            (str(correction["message_id"]),),
        ).fetchone()
    if message is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "message_not_found", "message": "Message assistant introuvable."},
        )
    job_payload = ExpertImprovementPayload(
        operation="compile",
        correction_id=UUID(str(correction["id"])),
        expected_revision=int(correction["revision"]),
        max_llm_requests=payload.max_llm_requests,
        conversation_id=UUID(str(message["conversation_id"])),
        client_request_id=payload.client_request_id,
        diagnosis_id=diagnosis_id,
        candidate_patch=payload.patch,
    )
    try:
        job = JobRepository(database.path).enqueue_expert_improvement(
            job_payload,
            user_message_id=UUID(str(correction["message_id"])),
        )
    except ExpertImprovementConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_improvement_idempotency_conflict", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_expert_candidate", "message": str(error)},
        ) from error
    return job.to_public()


@memory_router.get("/candidates/{candidate_id}")
def get_expert_candidate(
    candidate_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        candidate = ExpertCandidateCompiler(database).get(candidate_id)
    except ExpertCandidateError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_expert_candidate", "message": str(error)},
        ) from error
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "candidate_not_found", "message": "Candidat introuvable."},
        )
    return candidate


@memory_router.get("/candidates")
def list_expert_candidates(
    database: Annotated[Database, Depends(get_database)],
    candidate_state: Annotated[str | None, Query(alias="state", max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    """Expose only candidate states permitted for the requested review view."""

    allowed_states = {
        "draft",
        "structurally_valid",
        "evaluating",
        "awaiting_review",
        "approved",
        "activated",
        "needs_expert",
        "evaluation_failed",
        "rejected",
        "superseded",
        "inconclusive",
    }
    if candidate_state is not None and candidate_state not in allowed_states:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_candidate_state", "message": "État candidat invalide."},
        )
    clauses = ["c.id IS NOT NULL"]
    parameters: list[object] = []
    if candidate_state is not None:
        clauses.append("c.state = ?")
        parameters.append(candidate_state)
    where = " AND ".join(clauses)
    with database.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT c.id, c.state, c.base_release_id, c.candidate_release_id,
                   c.diff_sha256, c.created_at, c.updated_at,
                   release.package_sha256 AS candidate_sha256,
                   evaluation.id AS evaluation_id,
                   evaluation.state AS evaluation_state,
                   evaluation.report_sha256 AS evaluation_sha256,
                   pilot.id AS pilot_id,
                   pilot.state AS pilot_state,
                   active.generation AS active_generation
            FROM expert_candidates AS c
            JOIN expert_releases AS release ON release.id = c.candidate_release_id
            LEFT JOIN expert_evaluations AS evaluation
              ON evaluation.id = (
                  SELECT e.id FROM expert_evaluations AS e
                  WHERE e.candidate_id = c.id
                  ORDER BY e.created_at DESC, e.id DESC LIMIT 1
              )
            LEFT JOIN expert_pilots AS pilot
              ON pilot.id = (
                  SELECT p.id FROM expert_pilots AS p
                  WHERE p.candidate_id = c.id
                  ORDER BY p.updated_at DESC, p.id DESC LIMIT 1
              )
            JOIN expert_active_release AS active ON active.singleton = 1
            WHERE {where}
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT ?
            """,
            (*parameters, limit),
        ).fetchall()
    return {
        "candidates": [
            {
                "id": UUID(str(row["id"])),
                "state": row["state"],
                "base_release_id": UUID(str(row["base_release_id"])),
                "candidate_release_id": UUID(str(row["candidate_release_id"])),
                "candidate_sha256": row["candidate_sha256"],
                "diff_sha256": row["diff_sha256"],
                "evaluation_id": UUID(str(row["evaluation_id"]))
                if row["evaluation_id"] is not None
                else None,
                "evaluation_state": row["evaluation_state"],
                "evaluation_sha256": row["evaluation_sha256"],
                "pilot_id": UUID(str(row["pilot_id"])) if row["pilot_id"] is not None else None,
                "pilot_state": row["pilot_state"],
                "active_generation": int(row["active_generation"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    }


@memory_router.post(
    "/candidates/{candidate_id}/reviews",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_admin_profile)],
)
def review_expert_candidate(
    candidate_id: UUID,
    payload: ExpertReviewCreate,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        result, created = create_review(database, candidate_id, payload)
    except ExpertReviewConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_review_idempotency_conflict", "message": str(error)},
        ) from error
    except ExpertReviewError as error:
        code = str(error)
        status_code = (
            409
            if code
            in {
                "candidate_not_awaiting_review",
                "evaluation_not_passed",
            }
            else 422
        )
        raise HTTPException(
            status_code=status_code, detail={"code": code, "message": code}
        ) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@memory_router.post("/candidates/{candidate_id}/activation", dependencies=[Depends(_admin_profile)])
def activate_expert_candidate(
    candidate_id: UUID,
    payload: ExpertActivationRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return activate_candidate(database, candidate_id, payload)
    except ExpertReviewConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_activation_idempotency_conflict", "message": str(error)},
        ) from error
    except ExpertReviewError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code == "candidate_or_review_not_found" else 409,
            detail={"code": code, "message": code},
        ) from error


@memory_router.post("/rollback", dependencies=[Depends(_admin_profile)])
def rollback_expert_candidate(
    payload: ExpertRollbackRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return rollback_active_candidate(database, payload)
    except ExpertReviewConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_rollback_idempotency_conflict", "message": str(error)},
        ) from error
    except ExpertReviewError as error:
        code = str(error)
        raise HTTPException(status_code=409, detail={"code": code, "message": code}) from error


@memory_router.post(
    "/releases/{release_id}/rollback",
    dependencies=[Depends(_admin_profile)],
)
def rollback_expert_release(
    release_id: UUID,
    payload: ExpertRollbackRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    if release_id != payload.target_release_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rollback_target_conflict",
                "message": "La release du chemin ne correspond pas à la cible.",
            },
        )
    try:
        return rollback_active_candidate(database, payload)
    except ExpertReviewConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_rollback_idempotency_conflict", "message": str(error)},
        ) from error
    except ExpertReviewError as error:
        code = str(error)
        raise HTTPException(status_code=409, detail={"code": code, "message": code}) from error


@memory_router.get("/distributions")
def list_expert_distributions(
    database: Annotated[Database, Depends(get_database)],
    distribution_status: Annotated[str | None, Query(alias="state", max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    try:
        distributions = list_expert_memory_distributions(
            database, state=distribution_status, limit=limit
        )
    except ExpertMemoryPackageError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": str(error), "message": str(error)},
        ) from error
    return {"distributions": distributions}


@memory_router.get("/releases")
def list_expert_releases(
    database: Annotated[Database, Depends(get_database)],
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, object]:
    try:
        return list_expert_memory_releases(database, cursor=cursor, limit=limit)
    except ExpertMemoryPackageError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": str(error), "message": "Curseur de releases invalide."},
        ) from error


@memory_router.get("/distributions/{distribution_id}")
def get_expert_distribution(
    distribution_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    distribution = get_expert_memory_distribution(database, distribution_id)
    if distribution is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "distribution_not_found", "message": "Distribution introuvable."},
        )
    return distribution


@memory_router.post("/distributions/{distribution_id}/proposal")
def propose_expert_distribution(
    distribution_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return propose_expert_memory_distribution(database, distribution_id)
    except ExpertMemoryPackageError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code == "distribution_not_found" else 409,
            detail={"code": code, "message": code},
        ) from error


@memory_router.post(
    "/distributions/{distribution_id}/approval", dependencies=[Depends(_admin_profile)]
)
def approve_expert_distribution(
    distribution_id: UUID,
    payload: ExpertDistributionApprovalRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return approve_expert_memory_distribution(
            database,
            distribution_id,
            reviewer_label=payload.reviewer_label,
            reason=payload.reason,
        )
    except ExpertMemoryPackageError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code == "distribution_not_found" else 409,
            detail={"code": code, "message": code},
        ) from error


@memory_router.post(
    "/distributions/{distribution_id}/activation", dependencies=[Depends(_admin_profile)]
)
def activate_expert_distribution(
    distribution_id: UUID,
    payload: ExpertDistributionActivationRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return activate_expert_memory_distribution(
            database,
            distribution_id,
            client_request_id=payload.client_request_id,
            expected_active_generation=payload.expected_active_generation,
            expected_active_release_id=payload.expected_active_release_id,
        )
    except ExpertMemoryPackageError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code == "distribution_not_found" else 409,
            detail={"code": code, "message": code},
        ) from error


@memory_router.post(
    "/distributions/{distribution_id}/rollback", dependencies=[Depends(_admin_profile)]
)
def rollback_expert_distribution(
    distribution_id: UUID,
    payload: ExpertDistributionRollbackRequest,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return rollback_expert_memory_distribution(
            database,
            distribution_id,
            client_request_id=payload.client_request_id,
            target_release_id=payload.target_release_id,
            target_release_sha256=payload.target_release_sha256,
            expected_active_generation=payload.expected_active_generation,
            expected_active_release_id=payload.expected_active_release_id,
            reason=payload.reason,
        )
    except ExpertMemoryPackageError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code == "distribution_not_found" else 409,
            detail={"code": code, "message": code},
        ) from error


@memory_router.get("/evaluations/{evaluation_id}")
def get_expert_evaluation(
    evaluation_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    evaluation = get_evaluation(database, evaluation_id)
    if evaluation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "message": "Évaluation introuvable."},
        )
    return evaluation


@memory_router.post("/evaluations/{evaluation_id}/pilot-plans", status_code=status.HTTP_201_CREATED)
def create_expert_pilot_plan(
    evaluation_id: UUID,
    payload: ExpertPilotPlanRequest,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        result, created = create_pilot_plan(
            database,
            evaluation_id,
            pilot_id=payload.pilot_id,
        )
    except ExpertPilotStoreError as error:
        code = str(error)
        status_code = 404 if code == "evaluation_not_found" else 409
        raise HTTPException(
            status_code=status_code, detail={"code": code, "message": code}
        ) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@memory_router.get("/pilots/{pilot_id}")
def get_expert_pilot(
    pilot_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    pilot = get_pilot(database, pilot_id)
    if pilot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "pilot_not_found", "message": "Pilote introuvable."},
        )
    return pilot


@memory_router.post("/pilots/{pilot_id}/audit")
def audit_expert_pilot_route(
    pilot_id: UUID,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    try:
        return audit_pilot(database, pilot_id)
    except ExpertPilotStoreError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code == "pilot_not_found" else 409,
            detail={"code": code, "message": code},
        ) from error


@memory_router.post("/pilots/{pilot_id}/attestation", status_code=status.HTTP_201_CREATED)
def attest_expert_pilot_route(
    pilot_id: UUID,
    payload: ExpertPilotAttestationRequest,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    pilot = get_pilot(database, pilot_id)
    if pilot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "pilot_not_found", "message": "Pilote introuvable."},
        )
    if pilot["audit"] is None or pilot["audit_sha256"] is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "pilot_audit_missing", "message": "L'audit du pilote est requis."},
        )
    try:
        attestation = attest_expert_pilot(
            ExpertPilotAudit.model_validate(pilot["audit"]),
            attestation_id=payload.attestation_id,
            reviewer_label=payload.reviewer_label,
            external_reference=payload.external_reference,
            decision=payload.decision,
            reason=payload.reason,
        )
        result, created = attest_pilot(database, pilot_id, attestation)
    except (ExpertPilotError, ExpertPilotStoreError, ValueError) as error:
        code = str(error)
        raise HTTPException(status_code=409, detail={"code": code, "message": code}) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@memory_router.post("/pilots/{pilot_id}/observations", status_code=status.HTTP_201_CREATED)
def record_expert_pilot_observation(
    pilot_id: UUID,
    payload: ExpertPilotObservationCreate,
    response: Response,
    database: Annotated[Database, Depends(get_database)],
) -> dict[str, object]:
    pilot = get_pilot(database, pilot_id)
    if pilot is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "pilot_not_found", "message": "Pilote introuvable."},
        )
    try:
        observation = ExpertPilotObservation(
            observation_id=payload.observation_id,
            pilot_id=pilot_id,
            case_sha256=payload.case_sha256,
            protocol_sha256=pilot["plan"]["measurement_protocol_sha256"],
            expert_time_seconds=payload.expert_time_seconds,
            diagnosis_human_corrected=payload.diagnosis_human_corrected,
            useful_effect=payload.useful_effect,
            false_gain=payload.false_gain,
            rollback_count=payload.rollback_count,
            prompt_tokens=payload.prompt_tokens,
            completion_tokens=payload.completion_tokens,
            recorded_by=payload.recorded_by,
        )
        result, created = record_pilot_observation(database, pilot_id, observation)
    except (ExpertPilotStoreError, ValueError) as error:
        code = str(error)
        raise HTTPException(status_code=409, detail={"code": code, "message": code}) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@memory_router.post(
    "/candidates/{candidate_id}/evaluation-jobs",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_admin_profile)],
)
def enqueue_expert_candidate_evaluation(
    candidate_id: UUID,
    payload: ExpertEvaluationJobRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobPublic:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT id
            FROM expert_evaluations
            WHERE candidate_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (str(candidate_id),),
        ).fetchone()
    if row is None:
        with database.connect() as connection:
            candidate = connection.execute(
                "SELECT id FROM expert_candidates WHERE id = ?",
                (str(candidate_id),),
            ).fetchone()
        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "candidate_not_found", "message": "Candidat introuvable."},
            )
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "message": "Évaluation introuvable."},
        )
    return _enqueue_expert_evaluation(
        UUID(str(row["id"])), payload=payload, database=database, settings=settings
    )


@memory_router.post(
    "/evaluations/{evaluation_id}/evaluation-jobs",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_admin_profile)],
)
def enqueue_expert_evaluation(
    evaluation_id: UUID,
    payload: ExpertEvaluationJobRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobPublic:
    return _enqueue_expert_evaluation(
        evaluation_id, payload=payload, database=database, settings=settings
    )


@memory_router.post(
    "/evaluations/{evaluation_id}/orchestration-jobs",
    response_model=JobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_admin_profile)],
)
def enqueue_expert_evaluation_orchestration(
    evaluation_id: UUID,
    payload: ExpertEvaluationOrchestrationRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobPublic:
    evaluation = get_evaluation(database, evaluation_id)
    if evaluation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "message": "Évaluation introuvable."},
        )
    with database.connect() as connection:
        candidate = connection.execute(
            "SELECT state FROM expert_candidates WHERE id = ?",
            (str(evaluation["candidate_id"]),),
        ).fetchone()
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "candidate_not_found", "message": "Candidat introuvable."},
        )
    if candidate["state"] not in {"structurally_valid", "evaluating"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "candidate_state_conflict",
                "message": "Le candidat n'est pas prêt à être évalué.",
            },
        )
    pair_path = _evaluation_state_path(
        settings, payload.campaign_pair_path, file_name="campaign_pair.json"
    )
    if not pair_path.is_file():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evaluation_campaign_manifest_missing",
                "message": "Le manifeste de campagnes doit être persisté.",
            },
        )
    if candidate["state"] == "structurally_valid":
        try:
            ExpertCandidateCompiler(database).begin_evaluation(evaluation["candidate_id"])
        except ExpertCandidateError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "candidate_state_conflict", "message": str(error)},
            ) from error
    try:
        job = enqueue_evaluation_orchestrator_job(
            database,
            evaluation_id=evaluation_id,
            campaign_pair_path=pair_path,
            client_request_id=payload.client_request_id,
        )
    except ExpertEvaluationError as error:
        code = str(error)
        raise HTTPException(
            status_code=404 if code.endswith("not_found") else 409,
            detail={"code": code, "message": code},
        ) from error
    except ExpertImprovementConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_improvement_idempotency_conflict", "message": str(error)},
        ) from error
    return job.to_public()


def _enqueue_expert_evaluation(
    evaluation_id: UUID,
    *,
    payload: ExpertEvaluationJobRequest,
    database: Database,
    settings: Settings,
) -> JobPublic:
    """Validate the selected evaluation before creating its durable job."""

    evaluation = get_evaluation(database, evaluation_id)
    if evaluation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "message": "Évaluation introuvable."},
        )
    with database.connect() as connection:
        candidate = connection.execute(
            """
            SELECT c.state, c.diagnosis_id, d.correction_id, ec.revision,
                   m.id AS message_id, m.conversation_id
            FROM expert_candidates AS c
            JOIN expert_diagnoses AS d ON d.id = c.diagnosis_id
            JOIN expert_corrections AS ec ON ec.id = d.correction_id
            JOIN chat_messages AS m ON m.id = ec.message_id
            WHERE c.id = ?
            """,
            (str(evaluation["candidate_id"]),),
        ).fetchone()
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "candidate_not_found", "message": "Candidat introuvable."},
        )
    if candidate["state"] not in {"structurally_valid", "evaluating"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "candidate_state_conflict",
                "message": "Le candidat n'est pas prêt à être évalué.",
            },
        )
    base_path = _evaluation_state_path(settings, payload.base_state_path)
    candidate_path = _evaluation_state_path(settings, payload.candidate_state_path)
    if not base_path.is_file() or not candidate_path.is_file():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "evaluation_campaign_not_completed",
                "message": "Les deux états de campagne doivent être persistés.",
            },
        )
    if candidate["state"] == "structurally_valid":
        try:
            ExpertCandidateCompiler(database).begin_evaluation(evaluation["candidate_id"])
        except ExpertCandidateError as error:
            raise HTTPException(
                status_code=409,
                detail={"code": "candidate_state_conflict", "message": str(error)},
            ) from error
    try:
        job = enqueue_evaluation_comparison_job(
            database,
            evaluation_id=evaluation_id,
            base_state_path=base_path,
            candidate_state_path=candidate_path,
            conversation_id=UUID(str(candidate["conversation_id"])),
            user_message_id=UUID(str(candidate["message_id"])),
            client_request_id=payload.client_request_id,
        )
    except ExpertEvaluationEvidenceError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "evaluation_campaign_not_completed", "message": str(error)},
        ) from error
    except ExpertImprovementConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "expert_improvement_idempotency_conflict", "message": str(error)},
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_expert_evaluation", "message": str(error)},
        ) from error
    return job.to_public()


def _evaluation_state_path(
    settings: Settings, value: str, *, file_name: str = "state.json"
) -> Path:
    root = settings.paths.data_dir.resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_evaluation_path", "message": "Le chemin doit être relatif."},
        )
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_evaluation_path", "message": "Le chemin est hors données."},
        ) from error
    if resolved.name != file_name:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_evaluation_path",
                "message": f"Le fichier attendu est {file_name}.",
            },
        )
    return resolved
