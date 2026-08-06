from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.evaluation.chat_finetuning import audit_evaluation_run
from app.jobs.contracts import JobErrorKind
from app.jobs.repository import JobRepository


def test_evaluation_audit_accepts_visible_success_and_failure(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    start = datetime(2026, 8, 6, 8, tzinfo=UTC)
    first = repository.enqueue_evaluation_question(
        run_id="run-1",
        question_id="Q1",
        profile="p0",
        message="Question scientifique A",
        client_request_id=uuid4(),
        now=start,
    )
    claimed = repository.claim_next(
        worker_id="worker-eval",
        lease_duration=timedelta(minutes=5),
        now=start,
    )
    assert claimed is not None
    trace = {
        "run_id": "run-1",
        "question_id": "Q1",
        "profile": "p0",
        "question_sha256": first.job.payload.evaluation_question_sha256,
    }
    repository.persist_result_and_succeed(
        claimed.id,
        worker_id="worker-eval",
        assistant_content="Réponse scientifique A",
        assistant_response={
            "message": "Question scientifique A",
            "answer_markdown": "Réponse scientifique A",
            "evaluation": trace,
        },
        response_time_milliseconds=100,
        now=start + timedelta(minutes=1),
    )

    repository.enqueue_evaluation_question(
        run_id="run-1",
        question_id="Q2",
        profile="p0",
        message="Question scientifique B",
        client_request_id=uuid4(),
        now=start + timedelta(minutes=2),
    )
    failed_claim = repository.claim_next(
        worker_id="worker-eval",
        lease_duration=timedelta(minutes=5),
        now=start + timedelta(minutes=2),
    )
    assert failed_claim is not None
    repository.fail_attempt(
        failed_claim.id,
        worker_id="worker-eval",
        error_code=JobErrorKind.VALIDATION,
        safe_message="Réponse non validable.",
        diagnostic_code="invalid_schema",
        now=start + timedelta(minutes=3),
    )

    audit = audit_evaluation_run(repository.path, "run-1")

    assert audit.complete
    assert audit.reliable
    assert audit.unique_cells == 2
    assert audit.succeeded_cells == 1
    assert audit.maximum_concurrent_executions == 1
    assert audit.outcome_counts == {"failed": 1, "generated": 1}
    assert audit.problem_counts == {}


def test_evaluation_audit_detects_conversation_contamination(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    start = datetime(2026, 8, 6, 8, tzinfo=UTC)
    enqueued = repository.enqueue_evaluation_question(
        run_id="run-1",
        question_id="Q1",
        profile="p0",
        message="Question scientifique A",
        client_request_id=uuid4(),
        now=start,
    )
    with repository.database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages(id, conversation_id, position, role, content, created_at)
            VALUES (?, ?, 1, 'user', 'Question différente', ?)
            """,
            (str(uuid4()), str(enqueued.job.conversation_id), start.isoformat()),
        )
        connection.execute(
            """
            UPDATE jobs SET state = 'cancelled', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (start.isoformat(), start.isoformat(), str(enqueued.job.id)),
        )

    audit = audit_evaluation_run(repository.path, "run-1")

    assert not audit.reliable
    assert audit.problem_counts == {
        "conversation_question_contamination": 1,
        "terminal_job_without_visible_notice": 1,
    }
