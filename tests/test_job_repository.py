from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from app.jobs.contracts import (
    ChatAnswerPayload,
    JobErrorKind,
    JobState,
    JobStep,
    JobType,
    LongSynthesisPayload,
)
from app.jobs.repository import (
    EvaluationConversationIsolationError,
    EvaluationQuestionAlreadySubmittedError,
    EvaluationRunBusyError,
    JobRepository,
)


def _seed_user_message(repository: JobRepository) -> tuple[UUID, UUID]:
    conversation_id = uuid4()
    message_id = uuid4()
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO chat_conversations(id, title) VALUES (?, 'Test')",
            (str(conversation_id),),
        )
        connection.execute(
            """
            INSERT INTO chat_messages(id, conversation_id, position, role, content)
            VALUES (?, ?, 0, 'user', 'Question')
            """,
            (str(message_id), str(conversation_id)),
        )
    return conversation_id, message_id


def _seed_assistant_message(repository: JobRepository, conversation_id: UUID) -> UUID:
    message_id = uuid4()
    with repository.database.transaction() as connection:
        position = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM chat_messages WHERE conversation_id = ?",
            (str(conversation_id),),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO chat_messages(id, conversation_id, position, role, content)
            VALUES (?, ?, ?, 'assistant', 'Réponse')
            """,
            (str(message_id), str(conversation_id), position),
        )
    return message_id


def _seed_empty_conversation(repository: JobRepository) -> UUID:
    conversation_id = uuid4()
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO chat_conversations(id, title) VALUES (?, 'Evaluation')",
            (str(conversation_id),),
        )
    return conversation_id


def test_job_repository_uses_a_temporary_sqlite_file(tmp_path) -> None:
    database_path = tmp_path / "queue.sqlite3"
    repository = JobRepository(database_path)
    repository.initialize()

    assert repository.path == database_path
    with closing(repository.database.connect()) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {"jobs", "job_events"} <= tables


def test_enqueue_atomically_persists_job_and_initial_event(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    payload = ChatAnswerPayload(
        message="Question durable",
        conversation_id=conversation_id,
        client_request_id=uuid4(),
    )

    job = repository.enqueue(payload, user_message_id=message_id, priority=20, now=now)

    assert job.state is JobState.QUEUED
    assert job.step is JobStep.WAITING
    assert job.priority == 20
    assert job.payload == payload
    with closing(repository.database.connect()) as connection:
        event = connection.execute(
            "SELECT state, step, technical_message FROM job_events WHERE job_id = ?",
            (str(job.id),),
        ).fetchone()
    assert tuple(event) == ("queued", "waiting", "job.enqueued")


def test_enqueue_rolls_back_job_when_initial_event_fails(tmp_path, monkeypatch) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    payload = ChatAnswerPayload(
        message="Question durable",
        conversation_id=conversation_id,
        client_request_id=uuid4(),
    )

    def fail_event(*args, **kwargs) -> None:
        raise RuntimeError("simulated event failure")

    monkeypatch.setattr(repository, "_insert_initial_event", fail_event)
    with pytest.raises(RuntimeError, match="simulated event failure"):
        repository.enqueue(payload, user_message_id=message_id)

    with closing(repository.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 0


def test_atomic_chat_enqueue_rolls_back_user_message_when_job_event_fails(
    tmp_path, monkeypatch
) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id = uuid4()
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO chat_conversations(id, title) VALUES (?, 'Test')",
            (str(conversation_id),),
        )
    payload = ChatAnswerPayload(
        message="Question atomique",
        conversation_id=conversation_id,
        client_request_id=uuid4(),
    )

    def fail_event(*args, **kwargs) -> None:
        raise RuntimeError("simulated event failure")

    monkeypatch.setattr(repository, "_insert_initial_event", fail_event)
    with pytest.raises(RuntimeError, match="simulated event failure"):
        repository.enqueue_chat(payload)

    with closing(repository.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 0


def test_evaluation_submission_requires_an_idle_queue_and_fresh_conversation(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    first_conversation = _seed_empty_conversation(repository)
    first = repository.enqueue_chat(
        ChatAnswerPayload(
            message="Question A",
            conversation_id=first_conversation,
            client_request_id=uuid4(),
            interaction_mode="research",
            evaluation_run_id="run-1",
            evaluation_question_id="Q1",
            evaluation_profile="p0",
        )
    )

    busy_conversation = _seed_empty_conversation(repository)
    with pytest.raises(EvaluationRunBusyError):
        repository.enqueue_chat(
            ChatAnswerPayload(
                message="Question B",
                conversation_id=busy_conversation,
                client_request_id=uuid4(),
                interaction_mode="research",
                evaluation_run_id="run-1",
                evaluation_question_id="Q2",
                evaluation_profile="p0",
            )
        )

    normal_conversation = _seed_empty_conversation(repository)
    with pytest.raises(EvaluationRunBusyError):
        repository.enqueue_chat(
            ChatAnswerPayload(
                message="Question hors campagne",
                conversation_id=normal_conversation,
                client_request_id=uuid4(),
            )
        )

    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'failed', completed_at = updated_at WHERE id = ?",
            (str(first.job.id),),
        )

    with pytest.raises(EvaluationConversationIsolationError):
        repository.enqueue_chat(
            ChatAnswerPayload(
                message="Question B",
                conversation_id=first_conversation,
                client_request_id=uuid4(),
                interaction_mode="research",
                evaluation_run_id="run-1",
                evaluation_question_id="Q2",
                evaluation_profile="p0",
            )
        )

    duplicate_conversation = _seed_empty_conversation(repository)
    with pytest.raises(EvaluationQuestionAlreadySubmittedError):
        repository.enqueue_chat(
            ChatAnswerPayload(
                message="Question A",
                conversation_id=duplicate_conversation,
                client_request_id=uuid4(),
                interaction_mode="research",
                evaluation_run_id="run-1",
                evaluation_question_id="Q1",
                evaluation_profile="p0",
            )
        )


def test_evaluation_retry_preserves_global_single_job_execution(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    original = repository.enqueue_evaluation_question(
        run_id="run-retry",
        question_id="Q1",
        profile="p1",
        message="Question immuable",
        client_request_id=uuid4(),
    )
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'failed', completed_at = updated_at WHERE id = ?",
            (str(original.job.id),),
        )

    other_conversation, other_message = _seed_user_message(repository)
    other_job = repository.enqueue(
        ChatAnswerPayload(
            message="Autre travail actif",
            conversation_id=other_conversation,
            client_request_id=uuid4(),
        ),
        user_message_id=other_message,
    )

    with pytest.raises(EvaluationRunBusyError):
        repository.retry_failed(original.job.id, client_request_id=uuid4())

    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'failed', completed_at = updated_at WHERE id = ?",
            (str(other_job.id),),
        )
    retried = repository.retry_failed(original.job.id, client_request_id=uuid4())

    assert retried is not None
    assert retried.state is JobState.QUEUED
    assert retried.conversation_id == original.job.conversation_id
    assert retried.user_message_id == original.job.user_message_id
    assert retried.payload.evaluation_question_sha256 == (
        original.job.payload.evaluation_question_sha256
    )


def test_enqueue_retry_returns_the_existing_job(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    payload = ChatAnswerPayload(
        message="Question durable",
        conversation_id=conversation_id,
        client_request_id=uuid4(),
    )

    first = repository.enqueue(payload, user_message_id=message_id)
    retried = repository.enqueue(payload, user_message_id=message_id)

    assert retried.id == first.id
    with closing(repository.database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1


def test_get_job_returns_record_or_none(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    payload = ChatAnswerPayload(
        message="Question durable",
        conversation_id=conversation_id,
        client_request_id=uuid4(),
    )
    queued = repository.enqueue(payload, user_message_id=message_id)

    assert repository.get(queued.id) == queued
    assert repository.get(uuid4()) is None


def test_list_active_jobs_excludes_terminal_states(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)

    queued = repository.enqueue(
        ChatAnswerPayload(
            message="Question en attente",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )
    cancelling = repository.enqueue(
        ChatAnswerPayload(
            message="Question annulée",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )
    finished = repository.enqueue(
        ChatAnswerPayload(
            message="Question terminée",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'cancel_requested' WHERE id = ?", (str(cancelling.id),)
        )
        connection.execute("UPDATE jobs SET state = 'succeeded' WHERE id = ?", (str(finished.id),))

    active = repository.list_active(conversation_id)

    assert {job.id for job in active} == {queued.id, cancelling.id}
    assert all(job.state in {JobState.QUEUED, JobState.CANCEL_REQUESTED} for job in active)


def test_claim_next_atomically_leases_distinct_available_jobs(tmp_path) -> None:
    database_path = tmp_path / "queue.sqlite3"
    first_repository = JobRepository(database_path)
    first_repository.initialize()
    second_repository = JobRepository(database_path)
    conversation_id, message_id = _seed_user_message(first_repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    for number in range(2):
        first_repository.enqueue(
            ChatAnswerPayload(
                message=f"Question durable {number}",
                conversation_id=conversation_id,
                client_request_id=uuid4(),
            ),
            user_message_id=message_id,
            now=now - timedelta(seconds=1),
        )

    first = first_repository.claim_next(
        worker_id="worker-1", lease_duration=timedelta(minutes=2), now=now
    )
    second = second_repository.claim_next(
        worker_id="worker-2", lease_duration=timedelta(minutes=2), now=now
    )

    assert first is not None
    assert second is not None
    assert first.id != second.id
    assert first.state is JobState.RUNNING
    assert first.worker_id == "worker-1"
    assert first.attempt == 1
    assert second.worker_id == "worker-2"
    assert (
        first_repository.claim_next(
            worker_id="worker-3", lease_duration=timedelta(minutes=2), now=now
        )
        is None
    )


def test_claim_next_can_reserve_a_worker_for_chat_jobs(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    background = repository.enqueue_long_synthesis(
        LongSynthesisPayload(
            query_id="query-1",
            conversation_id=uuid4(),
            client_request_id=uuid4(),
        ),
        now=now - timedelta(seconds=2),
    )
    chat = repository.enqueue(
        ChatAnswerPayload(
            message="Question complète",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now - timedelta(seconds=1),
    )

    claimed = repository.claim_next(
        worker_id="chat-only",
        lease_duration=timedelta(minutes=2),
        job_types=(JobType.CHAT_ANSWER,),
        now=now,
    )

    assert claimed is not None
    assert claimed.id == chat.id
    assert repository.get(background.id).state is JobState.QUEUED


def test_heartbeat_renews_only_the_current_owner_lease(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    claimed = repository.claim_next(worker_id="owner", lease_duration=timedelta(minutes=2), now=now)
    assert claimed is not None

    assert (
        repository.heartbeat(
            claimed.id,
            worker_id="intruder",
            lease_duration=timedelta(minutes=5),
            now=now + timedelta(minutes=1),
        )
        is None
    )
    renewed = repository.heartbeat(
        claimed.id,
        worker_id="owner",
        lease_duration=timedelta(minutes=5),
        now=now + timedelta(minutes=1),
    )

    assert renewed is not None
    assert renewed.heartbeat_at == now + timedelta(minutes=1)
    assert renewed.lease_expires_at == now + timedelta(minutes=6)
    assert (
        repository.heartbeat(
            claimed.id,
            worker_id="owner",
            lease_duration=timedelta(minutes=5),
            now=now + timedelta(minutes=7),
        )
        is None
    )


def test_step_transition_and_event_are_atomic(tmp_path, monkeypatch) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    claimed = repository.claim_next(worker_id="owner", lease_duration=timedelta(minutes=5), now=now)
    assert claimed is not None

    assert (
        repository.transition_step(
            claimed.id,
            worker_id="intruder",
            step=JobStep.SEARCH,
            now=now + timedelta(seconds=1),
        )
        is None
    )
    searching = repository.transition_step(
        claimed.id,
        worker_id="owner",
        step=JobStep.SEARCH,
        now=now + timedelta(seconds=1),
    )
    assert searching is not None
    assert searching.step is JobStep.SEARCH

    def fail_event(*args, **kwargs) -> None:
        raise RuntimeError("simulated event failure")

    monkeypatch.setattr(repository, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="simulated event failure"):
        repository.transition_step(
            claimed.id,
            worker_id="owner",
            step=JobStep.ARGO,
            now=now + timedelta(seconds=2),
        )

    assert repository.get(claimed.id).step is JobStep.SEARCH
    with closing(repository.database.connect()) as connection:
        events = connection.execute(
            "SELECT state, step FROM job_events WHERE job_id = ? ORDER BY id",
            (str(claimed.id),),
        ).fetchall()
    assert [tuple(event) for event in events] == [
        ("queued", "waiting"),
        ("running", "waiting"),
        ("running", "search"),
    ]


def test_success_requires_a_persisted_assistant_result(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    claimed = repository.claim_next(worker_id="owner", lease_duration=timedelta(minutes=5), now=now)
    assert claimed is not None

    assert (
        repository.succeed(
            claimed.id,
            worker_id="owner",
            result_message_id=uuid4(),
            now=now + timedelta(seconds=1),
        )
        is None
    )
    result_message_id = _seed_assistant_message(repository, conversation_id)
    succeeded = repository.succeed(
        claimed.id,
        worker_id="owner",
        result_message_id=result_message_id,
        now=now + timedelta(seconds=2),
    )

    assert succeeded is not None
    assert succeeded.state is JobState.SUCCEEDED
    assert succeeded.step is JobStep.PERSISTENCE
    assert succeeded.result_message_id == result_message_id
    assert succeeded.completed_at == now + timedelta(seconds=2)
    assert succeeded.worker_id is None


def test_failure_is_bounded_and_retry_or_terminal_is_deterministic(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    first_attempt = repository.claim_next(
        worker_id="owner", lease_duration=timedelta(minutes=5), now=now
    )
    assert first_attempt is not None
    repository.transition_step(
        first_attempt.id,
        worker_id="owner",
        step=JobStep.SEARCH,
        now=now + timedelta(milliseconds=500),
    )

    retry = repository.fail_attempt(
        first_attempt.id,
        worker_id="owner",
        error_code=JobErrorKind.TIMEOUT,
        safe_message="x" * 1000,
        now=now + timedelta(seconds=1),
    )
    assert retry is not None
    assert retry.state is JobState.QUEUED
    assert retry.available_at == now + timedelta(seconds=31)
    assert retry.error_code is JobErrorKind.TIMEOUT
    assert len(retry.error_message) == 300

    second_attempt = repository.claim_next(
        worker_id="owner",
        lease_duration=timedelta(minutes=5),
        now=now + timedelta(seconds=31),
    )
    assert second_attempt is not None
    assert second_attempt.step is JobStep.WAITING
    terminal = repository.fail_attempt(
        second_attempt.id,
        worker_id="owner",
        error_code=JobErrorKind.AUTHENTICATION,
        safe_message="Clé ARGO à remplacer",
        now=now + timedelta(seconds=32),
    )

    assert terminal is not None
    assert terminal.state is JobState.FAILED
    assert terminal.error_code is JobErrorKind.AUTHENTICATION
    assert terminal.completed_at == now + timedelta(seconds=32)


def test_quota_defers_job_without_consuming_an_attempt(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    claimed = repository.claim_next(worker_id="owner", lease_duration=timedelta(minutes=5), now=now)
    assert claimed is not None and claimed.attempt == 1
    retry_at = now + timedelta(minutes=10)

    deferred = repository.defer_for_quota(
        claimed.id,
        worker_id="owner",
        retry_at=retry_at,
        now=now + timedelta(seconds=1),
    )

    assert deferred is not None
    assert deferred.state is JobState.QUEUED
    assert deferred.attempt == 0
    assert deferred.available_at == retry_at
    assert deferred.error_code is JobErrorKind.QUOTA
    assert (
        repository.claim_next(
            worker_id="early",
            lease_duration=timedelta(minutes=5),
            now=retry_at - timedelta(microseconds=1),
        )
        is None
    )
    resumed = repository.claim_next(
        worker_id="resumed", lease_duration=timedelta(minutes=5), now=retry_at
    )
    assert resumed is not None
    assert resumed.id == claimed.id


def test_queued_cancellation_prevents_future_claim(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    queued = repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )

    cancelled = repository.cancel_queued(queued.id, now=now + timedelta(seconds=1))

    assert cancelled is not None
    assert cancelled.state is JobState.CANCELLED
    assert cancelled.completed_at == now + timedelta(seconds=1)
    assert repository.cancel_queued(queued.id, now=now + timedelta(seconds=2)) is None
    assert (
        repository.claim_next(
            worker_id="owner",
            lease_duration=timedelta(minutes=5),
            now=now + timedelta(seconds=2),
        )
        is None
    )


def test_running_cancellation_is_honored_at_the_next_safe_boundary(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    claimed = repository.claim_next(worker_id="owner", lease_duration=timedelta(minutes=5), now=now)
    assert claimed is not None

    requested = repository.request_cancellation(claimed.id, now=now + timedelta(seconds=1))
    assert requested is not None
    assert requested.state is JobState.CANCEL_REQUESTED
    assert (
        repository.transition_step(
            claimed.id,
            worker_id="owner",
            step=JobStep.SEARCH,
            now=now + timedelta(seconds=2),
        )
        is None
    )
    assert (
        repository.acknowledge_cancellation(
            claimed.id,
            worker_id="intruder",
            now=now + timedelta(seconds=2),
        )
        is None
    )

    cancelled = repository.acknowledge_cancellation(
        claimed.id,
        worker_id="owner",
        now=now + timedelta(seconds=2),
    )
    assert cancelled is not None
    assert cancelled.state is JobState.CANCELLED
    assert cancelled.worker_id is None
    assert cancelled.completed_at == now + timedelta(seconds=2)


def test_expired_lease_recovery_is_deterministic(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    for number in range(3):
        repository.enqueue(
            ChatAnswerPayload(
                message=f"Question durable {number}",
                conversation_id=conversation_id,
                client_request_id=uuid4(),
            ),
            user_message_id=message_id,
            now=now + timedelta(microseconds=number),
        )
    claimed = [
        repository.claim_next(
            worker_id=f"worker-{number}",
            lease_duration=timedelta(minutes=1),
            now=now + timedelta(seconds=1),
        )
        for number in range(3)
    ]
    assert all(job is not None for job in claimed)
    first, second, third = claimed
    assert first is not None and second is not None and third is not None
    repository.request_cancellation(second.id, now=now + timedelta(seconds=2))
    with repository.database.transaction() as connection:
        connection.execute("UPDATE jobs SET attempt = 3 WHERE id = ?", (str(third.id),))

    recovered_at = now + timedelta(minutes=2)
    summary = repository.recover_expired_leases(now=recovered_at)

    assert summary.requeued == (first.id,)
    assert summary.cancelled == (second.id,)
    assert summary.failed == (third.id,)
    requeued = repository.get(first.id)
    cancelled = repository.get(second.id)
    failed = repository.get(third.id)
    assert requeued is not None and requeued.state is JobState.QUEUED
    assert requeued.available_at == recovered_at + timedelta(seconds=30)
    assert cancelled is not None and cancelled.state is JobState.CANCELLED
    assert failed is not None and failed.state is JobState.FAILED


def test_claim_order_respects_priority_fifo_and_future_availability(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    base = datetime(2026, 7, 22, 12, tzinfo=UTC)

    def enqueue(label: str, priority: int, offset: int, *, future: bool = False):
        return repository.enqueue(
            ChatAnswerPayload(
                message=label,
                conversation_id=conversation_id,
                client_request_id=uuid4(),
            ),
            user_message_id=message_id,
            priority=priority,
            now=base + timedelta(microseconds=offset),
            available_at=base + timedelta(hours=1) if future else base,
        )

    fifo_first = enqueue("FIFO premier", 20, 0)
    fifo_second = enqueue("FIFO second", 20, 1)
    priority_first = enqueue("Prioritaire", 10, 2)
    future = enqueue("Prioritaire futur", 0, 3, future=True)

    claimed_ids = []
    for number in range(3):
        claimed = repository.claim_next(
            worker_id=f"worker-{number}",
            lease_duration=timedelta(minutes=5),
            now=base + timedelta(seconds=1),
        )
        assert claimed is not None
        claimed_ids.append(claimed.id)

    assert claimed_ids == [priority_first.id, fifo_first.id, fifo_second.id]
    assert (
        repository.claim_next(
            worker_id="worker-early",
            lease_duration=timedelta(minutes=5),
            now=base + timedelta(minutes=59),
        )
        is None
    )
    claimed_future = repository.claim_next(
        worker_id="worker-future",
        lease_duration=timedelta(minutes=5),
        now=base + timedelta(hours=1),
    )
    assert claimed_future is not None
    assert claimed_future.id == future.id


def test_two_concurrent_claims_never_lease_the_same_job(tmp_path) -> None:
    database_path = tmp_path / "queue.sqlite3"
    repository = JobRepository(database_path)
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    queued = repository.enqueue(
        ChatAnswerPayload(
            message="Question concurrente",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    barrier = Barrier(2)

    def claim(worker_id: str):
        contender = JobRepository(database_path)
        barrier.wait(timeout=5)
        return contender.claim_next(
            worker_id=worker_id,
            lease_duration=timedelta(minutes=5),
            now=now,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-1", "worker-2")))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].id == queued.id
    assert sum(result is None for result in results) == 1


def test_crash_after_argo_before_persistence_recovers_without_duplicate_response(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id, message_id = _seed_user_message(repository)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    crashed_attempt = repository.claim_next(
        worker_id="worker-crashed",
        lease_duration=timedelta(minutes=1),
        now=now,
    )
    assert crashed_attempt is not None

    generated_but_not_persisted = "Réponse ARGO simulée"
    repository.recover_expired_leases(now=now + timedelta(minutes=2))
    resumed = repository.claim_next(
        worker_id="worker-resumed",
        lease_duration=timedelta(minutes=5),
        now=now + timedelta(minutes=2, seconds=30),
    )
    assert resumed is not None
    succeeded = repository.persist_result_and_succeed(
        resumed.id,
        worker_id="worker-resumed",
        assistant_content=generated_but_not_persisted,
        assistant_response={"answer_markdown": generated_but_not_persisted},
        response_time_milliseconds=1000,
        now=now + timedelta(minutes=2, seconds=31),
    )

    assert succeeded is not None
    assert succeeded.state is JobState.SUCCEEDED
    assert (
        repository.persist_result_and_succeed(
            resumed.id,
            worker_id="worker-resumed",
            assistant_content=generated_but_not_persisted,
            assistant_response={"answer_markdown": generated_but_not_persisted},
            response_time_milliseconds=1000,
            now=now + timedelta(minutes=2, seconds=32),
        )
        is None
    )
    with closing(repository.database.connect()) as connection:
        assistant_messages = connection.execute(
            """
            SELECT content FROM chat_messages
            WHERE conversation_id = ? AND role = 'assistant'
            """,
            (str(conversation_id),),
        ).fetchall()
    assert [row["content"] for row in assistant_messages] == [generated_but_not_persisted]
