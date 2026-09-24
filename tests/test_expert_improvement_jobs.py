from __future__ import annotations

import json
import time
from uuid import UUID, uuid4

import pytest

from app.database.sqlite import Database
from app.evaluation.campaign import EvaluationCampaignSpec
from app.evaluation.expert_memory import enqueue_evaluation_orchestrator_job
from app.expert_feedback.models import ExpertCorrectionCreate
from app.expert_feedback.repository import (
    ExpertCorrectionRepository,
)
from app.jobs.contracts import ExpertImprovementPayload, JobState, JobType
from app.jobs.expert_improvement_handler import ExpertImprovementHandler
from app.jobs.repository import ExpertImprovementConflictError, JobRepository
from app.jobs.worker import DurableJobWorker, JobHandlerRegistry


def _correction(settings) -> tuple[Database, dict[str, object], UUID]:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation = database.create_chat_conversation("Diagnostic durable")
    database.append_chat_message(
        conversation_id=conversation["id"], role="user", content="Question scientifique"
    )
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Réponse locale"
    )
    stored = database.chat_conversation(conversation["id"])
    assert stored is not None
    message_id = UUID(stored["messages"][1]["id"])
    correction, _ = ExpertCorrectionRepository(database).submit(
        message_id,
        ExpertCorrectionCreate(
            client_request_id=uuid4(),
            problem="La réponse doit expliciter sa méthode.",
            proposed_correction="Ajouter la méthode observée.",
            scope="this_answer",
        ),
    )
    return database, correction, message_id


def _conversation_id(database: Database, message_id: UUID) -> UUID:
    with database.connect() as connection:
        value = connection.execute(
            "SELECT conversation_id FROM chat_messages WHERE id = ?", (str(message_id),)
        ).fetchone()[0]
    return UUID(str(value))


def test_expert_improvement_enqueue_is_idempotent_and_conflict_safe(settings) -> None:
    database, correction, message_id = _correction(settings)
    repository = JobRepository(settings.paths.database_path)
    payload = ExpertImprovementPayload(
        correction_id=UUID(str(correction["id"])),
        expected_revision=1,
        conversation_id=_conversation_id(database, message_id),
        client_request_id=uuid4(),
    )

    first = repository.enqueue_expert_improvement(payload, user_message_id=message_id)
    replay = repository.enqueue_expert_improvement(payload, user_message_id=message_id)
    assert first.id == replay.id
    with pytest.raises(ExpertImprovementConflictError):
        repository.enqueue_expert_improvement(
            payload.model_copy(update={"expected_revision": 2}), user_message_id=message_id
        )


def test_expert_improvement_worker_persists_diagnosis_without_chat_message(settings) -> None:
    database, correction, message_id = _correction(settings)
    repository = JobRepository(settings.paths.database_path)
    payload = ExpertImprovementPayload(
        correction_id=UUID(str(correction["id"])),
        expected_revision=1,
        conversation_id=_conversation_id(database, message_id),
        client_request_id=uuid4(),
    )
    queued = repository.enqueue_expert_improvement(payload, user_message_id=message_id)
    before = len(database.chat_conversation(str(payload.conversation_id))["messages"])
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {JobType.EXPERT_IMPROVEMENT: ExpertImprovementHandler(database)}
        ),
        worker_id="expert-test",
    )

    completed = worker.run_once()

    assert completed is not None
    assert completed.id == queued.id
    assert completed.state is JobState.SUCCEEDED
    assert len(database.chat_conversation(str(payload.conversation_id))["messages"]) == before
    with database.connect() as connection:
        diagnosis = connection.execute(
            "SELECT COUNT(*) FROM expert_diagnoses WHERE correction_id = ?",
            (str(payload.correction_id),),
        ).fetchone()[0]
        status = connection.execute(
            "SELECT status FROM expert_corrections WHERE id = ?",
            (str(payload.correction_id),),
        ).fetchone()[0]
    assert diagnosis == 1
    assert status == "needs_expert"


def test_expert_improvement_orchestrator_defers_while_child_cell_runs(settings, tmp_path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation = database.create_chat_conversation("Évaluation orchestrée")
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Ancre d'évaluation"
    )
    anchor = database.chat_conversation(conversation["id"])
    assert anchor is not None
    message_id = UUID(anchor["messages"][0]["id"])
    cell = {"question_id": "Q1", "profile": "p0", "message": "Question test"}
    pair_path = tmp_path / "campaign_pair.json"
    pair_path.write_text(
        json.dumps(
            {
                "base": EvaluationCampaignSpec(run_id="base", cells=[cell]).model_dump(mode="json"),
                "candidate": EvaluationCampaignSpec(run_id="candidate", cells=[cell]).model_dump(
                    mode="json"
                ),
            }
        ),
        encoding="utf-8",
    )
    repository = JobRepository(settings.paths.database_path)
    parent = repository.enqueue_expert_improvement(
        ExpertImprovementPayload(
            operation="orchestrate",
            evaluation_id=uuid4(),
            campaign_pair_path=str(pair_path),
            conversation_id=UUID(conversation["id"]),
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {JobType.EXPERT_IMPROVEMENT: ExpertImprovementHandler(database)}
        ),
        worker_id="orchestrator-test",
    )

    deferred = worker.run_once()

    assert deferred is not None
    assert deferred.id == parent.id
    assert deferred.state is JobState.QUEUED
    with database.connect() as connection:
        child = connection.execute(
            """
            SELECT id, state, json_extract(payload_json, '$.orchestrator_parent_job_id')
            FROM jobs
            WHERE type = ? AND json_extract(payload_json, '$.evaluation_run_id') IS NOT NULL
            """,
            (JobType.CHAT_ANSWER.value,),
        ).fetchone()
    assert child is not None
    assert child[2] == str(parent.id)
    assert child[1] == JobState.QUEUED.value

    cancelled_parent = repository.cancel_orchestrator(parent.id)

    assert cancelled_parent is not None
    assert cancelled_parent.state is JobState.CANCELLED
    assert repository.get(UUID(child[0])).state is JobState.CANCELLED


def test_expert_improvement_orchestrator_completes_pair_and_enqueues_comparison(
    settings, expert_package_payload, tmp_path
) -> None:
    from tests.test_evaluation_campaign import SuccessfulEvaluationHandler
    from tests.test_expert_review import _prepared_candidate

    database, _candidate_id, _candidate_release, evaluation_id, _report = _prepared_candidate(
        settings, expert_package_payload
    )
    pair_path = tmp_path / "campaign_pair.json"
    pair_path.write_text(
        json.dumps(
            {
                "base": EvaluationCampaignSpec(
                    run_id="full-base",
                    cells=[
                        {
                            "question_id": "Q1",
                            "profile": "p0",
                            "message": "Question test",
                        }
                    ],
                ).model_dump(mode="json"),
                "candidate": EvaluationCampaignSpec(
                    run_id="full-candidate",
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
    parent = enqueue_evaluation_orchestrator_job(
        database,
        evaluation_id=evaluation_id,
        campaign_pair_path=pair_path,
        client_request_id=uuid4(),
    )
    repository = JobRepository(settings.paths.database_path)
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                JobType.CHAT_ANSWER: SuccessfulEvaluationHandler(),
                JobType.EXPERT_IMPROVEMENT: ExpertImprovementHandler(database),
            }
        ),
        worker_id="orchestrator-full-cycle",
    )

    first_parent = worker.run_once()
    assert first_parent is not None and first_parent.state is JobState.QUEUED
    first_child = worker.run_once()
    assert first_child is not None and first_child.type is JobType.CHAT_ANSWER
    time.sleep(1.1)
    second_parent = worker.run_once()
    assert second_parent is not None and second_parent.state is JobState.QUEUED
    second_child = worker.run_once()
    assert second_child is not None and second_child.type is JobType.CHAT_ANSWER
    time.sleep(1.1)
    completed_parent = worker.run_once()

    assert completed_parent is not None
    assert completed_parent.id == parent.id
    assert completed_parent.state is JobState.SUCCEEDED
    with database.connect() as connection:
        comparison = connection.execute(
            """
            SELECT state, json_extract(payload_json, '$.operation')
            FROM jobs
            WHERE type = 'expert_improvement' AND id != ?
            """,
            (str(parent.id),),
        ).fetchone()
    assert comparison is not None
    assert comparison[0] == JobState.QUEUED.value
    assert comparison[1] == "evaluate"
