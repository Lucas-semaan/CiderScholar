"""Durable handler for deterministic private expert-feedback diagnosis."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

from app.database.sqlite import Database
from app.evaluation.campaign import (
    EvaluationCampaignPairCoordinator,
    EvaluationCampaignRunner,
    EvaluationCampaignSpec,
)
from app.evaluation.expert_memory import evaluate_campaign_pair
from app.jobs.contracts import ExpertImprovementPayload, JobStep, JobType
from app.jobs.repository import JobRecord
from app.jobs.worker import JobHandlerDeferred, JobHandlerResult, JobProgressContext
from app.services.expert_improvement import ExpertImprovementService


@dataclass(slots=True)
class ExpertImprovementHandler:
    database: Database
    clock: Callable[[], float] = monotonic

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        if job.type is not JobType.EXPERT_IMPROVEMENT or not isinstance(
            job.payload, ExpertImprovementPayload
        ):
            raise ValueError("expert-improvement handler received another job type")
        started_at = self.clock()
        context.check_cancellation()
        if job.payload.operation == "diagnose":
            context.publish(JobStep.VERIFICATION, technical_message="expert_diagnosis.started")
            result, _created = ExpertImprovementService(self.database).diagnose_correction(
                job.payload.correction_id,
                expected_revision=job.payload.expected_revision,
            )
            response = {
                "diagnosis_id": str(result["id"]),
                "diagnosis_sha256": result["diagnosis_sha256"],
            }
        elif job.payload.operation == "compile":
            assert job.payload.diagnosis_id is not None
            assert job.payload.candidate_patch is not None
            context.publish(JobStep.PERSISTENCE, technical_message="expert_candidate.started")
            result, _created = ExpertImprovementService(self.database).compile_candidate(
                job.payload.candidate_patch,
                diagnosis_id=job.payload.diagnosis_id,
            )
            response = {
                "candidate_id": str(result["id"]),
                "diff_sha256": result["diff_sha256"],
                "candidate_release_id": str(result["candidate_release_id"]),
            }
        elif job.payload.operation == "evaluate":
            assert job.payload.evaluation_id is not None
            assert job.payload.base_state_path is not None
            assert job.payload.candidate_state_path is not None
            context.publish(JobStep.VALIDATION, technical_message="expert_evaluation.started")
            result, _created = evaluate_campaign_pair(
                self.database,
                job.payload.evaluation_id,
                base_state_path=job.payload.base_state_path,
                candidate_state_path=job.payload.candidate_state_path,
            )
            report = result["report"]
            response = {
                "evaluation_id": str(result["id"]),
                "evaluation_state": result["state"],
                "report_sha256": result["report_sha256"],
                "deterministic_validators_passed": (
                    report.get("deterministic_validators_passed")
                    if isinstance(report, dict)
                    else False
                ),
            }
        else:
            assert job.payload.evaluation_id is not None
            assert job.payload.campaign_pair_path is not None
            pair_path = Path(job.payload.campaign_pair_path)
            artifact = json.loads(pair_path.read_text(encoding="utf-8"))
            base_spec = EvaluationCampaignSpec.model_validate(artifact["base"])
            candidate_spec = EvaluationCampaignSpec.model_validate(artifact["candidate"])
            repository = context.repository
            coordinator = EvaluationCampaignPairCoordinator(
                EvaluationCampaignRunner(repository, pair_path.parent / "base", poll_seconds=0),
                EvaluationCampaignRunner(
                    repository, pair_path.parent / "candidate", poll_seconds=0
                ),
            )
            progress = coordinator.advance_one(
                base_spec,
                candidate_spec,
                exclude_active_job_id=job.id,
            )
            if not progress.comparison_ready:
                raise JobHandlerDeferred(
                    datetime.now(UTC) + timedelta(seconds=1),
                    "evaluation.waiting_for_cell",
                )
            comparison = coordinator.enqueue_comparison(
                evaluation_id=job.payload.evaluation_id,
                conversation_id=job.payload.conversation_id,
                user_message_id=job.user_message_id,
                client_request_id=uuid5(
                    NAMESPACE_URL,
                    f"ciderscholar:evaluation-comparison:{job.payload.evaluation_id}",
                ),
            )
            response = {
                "evaluation_id": str(job.payload.evaluation_id),
                "comparison_job_id": str(comparison.id),
                "comparison_job_state": comparison.state.value,
            }
        context.check_cancellation()
        return JobHandlerResult(
            assistant_content=(
                "Diagnostic expert enregistré."
                if job.payload.operation == "diagnose"
                else (
                    "Candidat expert isolé enregistré."
                    if job.payload.operation == "compile"
                    else (
                        "Orchestration de l'évaluation expert reprise."
                        if job.payload.operation == "orchestrate"
                        else "Rapport d'évaluation expert enregistré."
                    )
                )
            ),
            assistant_response=response,
            response_time_milliseconds=max(0.0, (self.clock() - started_at) * 1000),
            persist_conversation_result=False,
        )
