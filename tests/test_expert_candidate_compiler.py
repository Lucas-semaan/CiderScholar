from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.database.sqlite import Database
from app.evaluation.campaign import EvaluationCampaignSpec
from app.evaluation.expert_memory import ExpertEvaluationManifest, plan_evaluation
from app.expert_feedback.candidates import ExpertCandidateCompiler, ExpertCandidateError
from app.expert_feedback.models import (
    CandidatePatch,
    Diagnosis,
    ExpertCorrectionCreate,
    PatchOperation,
)
from app.expert_feedback.repository import ExpertCorrectionRepository
from app.jobs.contracts import ExpertImprovementPayload, JobType
from app.jobs.expert_improvement_handler import ExpertImprovementHandler
from app.jobs.repository import JobRepository
from app.jobs.worker import DurableJobWorker, JobHandlerRegistry
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository
from app.main import create_app


def _diagnosis(database: Database, item_id: str, item_hash: str) -> tuple[UUID, UUID, UUID, str]:
    conversation = database.create_chat_conversation("Correction candidate")
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Réponse test"
    )
    message = database.chat_conversation(conversation["id"])["messages"][0]
    correction, _ = ExpertCorrectionRepository(database).submit(
        UUID(message["id"]),
        ExpertCorrectionCreate(
            client_request_id=uuid4(),
            problem="Le résultat doit être mieux borné.",
            proposed_correction="Ajouter la borne observée.",
            scope="reusable_method",
        ),
    )
    payload = Diagnosis(
        correction_id=correction["id"],
        correction_revision=1,
        primary_cause="semantic_filter_error",
        observed_ids=(item_id,),
        verified_hashes=(item_hash,),
        rationale="Le candidat est autorisé par une trace de test.",
        confidence="supported",
        target_item_ids=(item_id,),
        proposed_action="knowledge_candidate",
    )
    diagnosis_id = uuid4()
    diagnosis_hash = hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest()
    now = datetime(2026, 9, 23, 12, tzinfo=UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO expert_diagnoses(
                id, correction_id, correction_revision, payload_json, diagnosis_sha256, created_at
            ) VALUES (?, ?, 1, ?, ?, ?)
            """,
            (
                str(diagnosis_id),
                str(correction["id"]),
                payload.model_dump_json(),
                diagnosis_hash,
                now,
            ),
        )
    return diagnosis_id, correction["id"], UUID(message["id"]), diagnosis_hash


def test_candidate_compiler_creates_isolated_release_and_replays(
    settings, expert_package_payload, monkeypatch
) -> None:
    from app.corpora import LOCAL_PROFILE_ENV

    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    database = Database(settings.paths.database_path)
    database.initialize()
    repository = KnowledgeRepository(database)
    base_package = KnowledgePackage.model_validate(expert_package_payload)
    base = repository.import_candidate(base_package)
    item = base_package.items[0]
    diagnosis_id, correction_id, message_id, diagnosis_hash = _diagnosis(
        database, item.id, item.content_sha256
    )
    replacement = item.model_copy(update={"body": "Version candidate bornée."})
    patch = CandidatePatch(
        base_release_id=base.id,
        diagnosis_sha256=diagnosis_hash,
        operations=(
            PatchOperation(
                operation="replace_item",
                item_id=item.id,
                expected_sha256=item.content_sha256,
                item=replacement,
                justification="Corriger la borne observée.",
            ),
        ),
    )
    compiler = ExpertCandidateCompiler(database)

    candidate, created = compiler.compile(patch, diagnosis_id=diagnosis_id)
    replay, replayed = compiler.compile(patch, diagnosis_id=diagnosis_id)

    assert created is True
    assert replayed is False
    assert replay == candidate
    assert candidate["state"] == "structurally_valid"
    evaluation_manifest = ExpertEvaluationManifest(
        candidate_id=candidate["id"],
        base_release_id=base.id,
        candidate_release_id=candidate["candidate_release_id"],
        corpus_snapshot_sha256="e" * 64,
        configuration_sha256="f" * 64,
        code_revision="candidate-api-test",
        split="development",
        question_hashes=("1" * 64,),
        control_question_hashes=("2" * 64, "3" * 64),
        mode="abstract_only",
        max_llm_requests=0,
        argo_authorized=False,
    )
    evaluation_record, _ = plan_evaluation(database, evaluation_manifest)
    evaluating = compiler.begin_evaluation(candidate["id"])
    assert evaluating["state"] == "evaluating"
    with pytest.raises(ExpertCandidateError, match="candidate_state_conflict"):
        compiler.begin_evaluation(candidate["id"])
    assert repository.resolve_active_release().release_id is None
    _stored, candidate_package = repository.load_release(candidate["candidate_release_id"])
    assert candidate_package.items[0].body == "Version candidate bornée."
    with database.connect() as connection:
        assert connection.execute("SELECT status FROM expert_corrections").fetchone()[0] == (
            "candidate_ready"
        )

    with database.connect() as connection:
        conversation_id = UUID(
            str(
                connection.execute(
                    "SELECT conversation_id FROM chat_messages WHERE id = ?",
                    (str(message_id),),
                ).fetchone()[0]
            )
        )
    queued = JobRepository(settings.paths.database_path).enqueue_expert_improvement(
        ExpertImprovementPayload(
            operation="compile",
            correction_id=correction_id,
            expected_revision=1,
            conversation_id=conversation_id,
            client_request_id=uuid4(),
            diagnosis_id=diagnosis_id,
            candidate_patch=patch,
        ),
        user_message_id=message_id,
    )
    completed = DurableJobWorker(
        repository=JobRepository(settings.paths.database_path),
        registry=JobHandlerRegistry(
            {JobType.EXPERT_IMPROVEMENT: ExpertImprovementHandler(database)}
        ),
        worker_id="candidate-test",
    ).run_once()
    assert completed is not None
    assert completed.id == queued.id

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/expert-memory/diagnoses/{diagnosis_id}/candidate-jobs",
            json={
                "client_request_id": str(uuid4()),
                "max_llm_requests": 0,
                "patch": patch.model_dump(mode="json"),
            },
        )
        state_payload = {
            "run_id": "candidate-api-run",
            "status": "completed",
            "cells": {
                "case": {"question_sha256": "1" * 64, "state": "succeeded"},
                "control-a": {"question_sha256": "2" * 64, "state": "succeeded"},
                "control-b": {"question_sha256": "3" * 64, "state": "succeeded"},
            },
        }
        base_state = settings.paths.data_dir / "evaluations" / "base" / "state.json"
        candidate_state = settings.paths.data_dir / "evaluations" / "candidate" / "state.json"
        base_state.parent.mkdir(parents=True, exist_ok=True)
        candidate_state.parent.mkdir(parents=True, exist_ok=True)
        base_state.write_text(json.dumps(state_payload), encoding="utf-8")
        candidate_state.write_text(
            json.dumps({**state_payload, "run_id": "candidate-api-run-candidate"}),
            encoding="utf-8",
        )
        pair_path = settings.paths.data_dir / "evaluations" / "campaign_pair.json"
        pair_path.write_text(
            json.dumps(
                {
                    "base": EvaluationCampaignSpec(
                        run_id="candidate-api-run-base",
                        cells=[
                            {
                                "question_id": "Q1",
                                "profile": "p0",
                                "message": "Question test",
                            }
                        ],
                    ).model_dump(mode="json"),
                    "candidate": EvaluationCampaignSpec(
                        run_id="candidate-api-run-candidate",
                        cells=[
                            {
                                "question_id": "Q1",
                                "profile": "p0",
                                "message": "Question test",
                            }
                        ],
                    ).model_dump(mode="json"),
                }
            ),
            encoding="utf-8",
        )
        orchestration_response = client.post(
            f"/api/expert-memory/evaluations/{evaluation_record['id']}/orchestration-jobs",
            json={
                "client_request_id": str(uuid4()),
                "campaign_pair_path": "evaluations/campaign_pair.json",
            },
        )
        evaluation_response = client.post(
            f"/api/expert-memory/candidates/{candidate['id']}/evaluation-jobs",
            json={
                "client_request_id": str(uuid4()),
                "base_state_path": "evaluations/base/state.json",
                "candidate_state_path": "evaluations/candidate/state.json",
                "max_llm_requests": 0,
            },
        )
        inspected = client.get(f"/api/expert-memory/candidates/{candidate['id']}")
    assert response.status_code == 202
    assert response.json()["type"] == "expert_improvement"
    assert evaluation_response.status_code == 202
    assert evaluation_response.json()["type"] == "expert_improvement"
    assert orchestration_response.status_code == 202, orchestration_response.json()
    assert orchestration_response.json()["type"] == "expert_improvement"
    assert inspected.status_code == 200
    assert inspected.json()["candidate_release_id"] == str(candidate["candidate_release_id"])


def test_candidate_compiler_rejects_uncertain_diagnosis(settings, expert_package_payload) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    repository = KnowledgeRepository(database)
    package = KnowledgePackage.model_validate(expert_package_payload)
    base = repository.import_candidate(package)
    item = package.items[0]
    diagnosis_id, _correction_id, _message_id, diagnosis_hash = _diagnosis(
        database, item.id, item.content_sha256
    )
    patch = CandidatePatch(
        base_release_id=base.id,
        diagnosis_sha256=diagnosis_hash,
        operations=(
            PatchOperation(
                operation="retire_item",
                item_id=item.id,
                expected_sha256=item.content_sha256,
                justification="Test de blocage.",
            ),
        ),
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE expert_diagnoses SET payload_json = ? WHERE id = ?",
            (
                Diagnosis(
                    correction_id=_correction_id,
                    correction_revision=1,
                    primary_cause="expert_ambiguity",
                    rationale="Décision humaine requise.",
                    confidence="uncertain",
                    proposed_action="expert_review",
                ).model_dump_json(),
                str(diagnosis_id),
            ),
        )
    with pytest.raises(ExpertCandidateError, match="diagnosis_not_supported"):
        ExpertCandidateCompiler(database).compile(patch, diagnosis_id=diagnosis_id)
