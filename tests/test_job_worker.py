from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from threading import Event
from time import sleep
from uuid import uuid4

import pytest

from app.corpora import LOCAL_PROFILE_ENV
from app.jobs.chat_handler import ChatAnswerHandler
from app.jobs.contracts import (
    ChatAnswerPayload,
    JobErrorKind,
    JobPublic,
    JobState,
    JobStep,
    JobType,
)
from app.jobs.repository import JobRepository
from app.jobs.worker import (
    DurableJobWorker,
    JobHandlerRegistry,
    JobHandlerResult,
    JobProgressContext,
    UnknownJobTypeError,
)
from app.llm.argo_client import (
    ArgoAuthenticationError,
    ArgoAuthorizationError,
    ArgoLocalQuotaError,
    ArgoQuotaError,
    ArgoScientificValidationError,
    ArgoUnavailableError,
)
from app.models.chatbot import ChatbotResult
from app.services.chatbot import ChatbotNoSourcesError


def _claimed_job(
    repository: JobRepository,
    now: datetime,
    *,
    use_external_sources: bool = False,
):
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
    repository.enqueue(
        ChatAnswerPayload(
            message="Question durable",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
            use_external_sources=use_external_sources,
        ),
        user_message_id=message_id,
        now=now,
    )
    job = repository.claim_next(
        worker_id="worker-test",
        lease_duration=timedelta(minutes=5),
        now=now,
    )
    assert job is not None
    return job


def test_fake_handler_uses_progress_context_without_argo(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    context = JobProgressContext(
        repository=repository,
        job_id=job.id,
        worker_id="worker-test",
        clock=lambda: now + timedelta(seconds=1),
    )

    class FakeHandler:
        def handle(self, leased_job, progress) -> JobHandlerResult:
            assert leased_job.id == job.id
            progress.publish(JobStep.SEARCH)
            return JobHandlerResult(
                assistant_content="Réponse simulée",
                assistant_response={"answer_markdown": "Réponse simulée"},
                response_time_milliseconds=10,
            )

    result = FakeHandler().handle(job, context)

    assert result.assistant_content == "Réponse simulée"
    assert repository.get(job.id).step is JobStep.SEARCH


def test_worker_renews_lease_while_a_long_handler_runs(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime.now(UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class SlowHandler:
        def handle(self, _job, _context) -> JobHandlerResult:
            sleep(0.35)
            return JobHandlerResult(
                assistant_content="Résultat après traitement long.",
                assistant_response={"ok": True},
                response_time_milliseconds=350,
            )

    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: SlowHandler()}),
        worker_id="heartbeat-test",
        lease_duration=timedelta(seconds=0.15),
    )
    completed = worker.run_once()

    assert completed is not None
    assert completed.state.value == "succeeded"


def test_closed_handler_registry_rejects_unknown_type_before_execution() -> None:
    calls = 0

    class FakeHandler:
        def handle(self, job, context) -> JobHandlerResult:
            nonlocal calls
            calls += 1
            raise AssertionError("handler should not execute")

    registry = JobHandlerRegistry({JobType.CHAT_ANSWER: FakeHandler()})

    assert registry.resolve(JobType.CHAT_ANSWER).__class__ is FakeHandler
    with pytest.raises(UnknownJobTypeError, match="unknown job type"):
        registry.resolve("unknown")
    assert calls == 0


def test_run_once_claims_at_most_one_job_and_empty_queue_is_clean(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    first = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(first.id),),
        )
    calls = 0

    class FakeHandler:
        def handle(self, job, context) -> JobHandlerResult:
            nonlocal calls
            calls += 1
            context.publish(JobStep.SEARCH)
            return JobHandlerResult(
                assistant_content="Réponse simulée",
                assistant_response={"answer_markdown": "Réponse simulée"},
                response_time_milliseconds=10,
            )

    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: FakeHandler()}),
        worker_id="worker-test",
        clock=lambda: now + timedelta(seconds=1),
    )

    completed = worker.run_once()
    assert completed is not None
    assert calls == 1
    assert worker.run_once() is None
    assert calls == 1


def test_continuous_worker_stops_cleanly_and_closes_handlers(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )
    stop_event = Event()

    class ClosingHandler:
        closed = False

        def handle(self, job, context) -> JobHandlerResult:
            stop_event.set()
            return JobHandlerResult(
                assistant_content="Réponse simulée",
                assistant_response={"answer_markdown": "Réponse simulée"},
                response_time_milliseconds=10,
            )

        def close(self) -> None:
            self.closed = True

    handler = ClosingHandler()
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: handler}),
        worker_id="worker-test",
        clock=lambda: now + timedelta(seconds=1),
    )

    completed_count = worker.run_forever(stop_event, idle_seconds=0.01)

    assert completed_count == 1
    assert handler.closed is True


def test_continuous_worker_retries_transient_database_contention(tmp_path, caplog) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    stop_event = Event()

    class ContendedWorker(DurableJobWorker):
        calls = 0

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("database is locked")
            stop_event.set()
            return None

    worker = ContendedWorker(
        repository=repository,
        registry=JobHandlerRegistry({}),
        worker_id="contention-test",
    )

    with caplog.at_level(logging.WARNING, logger="ciderscholar.jobs.worker"):
        completed_count = worker.run_forever(stop_event, idle_seconds=0.01)

    assert completed_count == 0
    assert worker.calls == 2
    assert "job_database_contention_retry worker_id=contention-test" in caplog.messages


def test_continuous_worker_does_not_hide_unrelated_database_errors(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()

    class BrokenWorker(DurableJobWorker):
        def run_once(self):
            raise sqlite3.OperationalError("malformed database schema")

    worker = BrokenWorker(
        repository=repository,
        registry=JobHandlerRegistry({}),
        worker_id="database-error-test",
    )

    with pytest.raises(sqlite3.OperationalError, match="malformed database schema"):
        worker.run_forever(Event(), idle_seconds=0.01)


def test_default_worker_id_is_process_stable_and_not_public(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    registry = JobHandlerRegistry({})

    first = DurableJobWorker(repository=repository, registry=registry)
    second = DurableJobWorker(repository=repository, registry=registry)

    assert first.worker_id == second.worker_id
    assert first.worker_id.startswith("worker-")
    assert "worker_id" not in JobPublic.model_fields


def test_worker_recovers_expired_leases_before_new_claim(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    expired = _claimed_job(repository, now)
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({}),
        worker_id="recovery-worker",
        clock=lambda: now + timedelta(minutes=6),
    )

    assert worker.run_once() is None
    recovered = repository.get(expired.id)
    assert recovered is not None
    assert recovered.state.value == "queued"
    assert recovered.worker_id is None
    assert recovered.available_at == now + timedelta(minutes=6, seconds=30)

    class RestartedHandler:
        def handle(self, job, context) -> JobHandlerResult:
            return JobHandlerResult(
                assistant_content="Réponse après redémarrage",
                assistant_response={"answer_markdown": "Réponse après redémarrage"},
                response_time_milliseconds=10,
            )

    resumed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: RestartedHandler()}),
        worker_id="restarted-worker",
        clock=lambda: now + timedelta(minutes=6, seconds=30),
    ).run_once()

    assert resumed is not None
    assert resumed.id == expired.id
    assert resumed.state.value == "succeeded"
    with repository.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 2


def test_secondary_worker_leaves_lease_recovery_to_the_coordinator(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    expired = _claimed_job(repository, now)
    secondary = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({}),
        worker_id="secondary-worker",
        clock=lambda: now + timedelta(minutes=6),
        lease_recovery_enabled=False,
    )

    assert secondary.run_once() is None
    unchanged = repository.get(expired.id)
    assert unchanged is not None
    assert unchanged.state is JobState.RUNNING
    assert unchanged.worker_id == expired.worker_id


def test_chat_handler_delegates_to_existing_answer_chatbot_workflow(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    calls = []

    def fake_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        on_progress("planning")
        persisted = repository.get(job.id)
        assert persisted is not None
        assert persisted.step is JobStep.PLANNING
        on_argo_reserved()
        persisted = repository.get(job.id)
        assert persisted is not None
        assert persisted.step is JobStep.PLANNING
        for stage, expected in (
            ("search", JobStep.SEARCH),
            ("reranking", JobStep.RERANKING),
            ("evidence_selection", JobStep.EVIDENCE_SELECTION),
            ("coverage", JobStep.COVERAGE),
            ("generation", JobStep.GENERATION),
        ):
            on_progress(stage)
            persisted = repository.get(job.id)
            assert persisted is not None
            assert persisted.step is expected
        on_argo_response()
        calls.append((active_settings, database, message, history, use_external_sources))
        return ChatbotResult(
            message=message,
            retrieval_query=message,
            answer_markdown="Réponse existante",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=2,
            duration_seconds=0.5,
        )

    handler = ChatAnswerHandler(
        settings=settings,
        database=repository.database,
        answer=fake_answer,
    )
    context = JobProgressContext(
        repository=repository,
        job_id=job.id,
        worker_id="worker-test",
        clock=lambda: now + timedelta(seconds=1),
    )

    result = handler.handle(job, context)

    assert result.assistant_content == "Réponse existante"
    assert result.response_time_milliseconds == 500
    assert len(calls) == 1
    assert calls[0][2:] == ("Question durable", [], False)


def test_evaluation_job_pins_profile_and_persists_cell_identity(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    conversation = repository.database.create_chat_conversation("Evaluation Q1")
    enqueued = repository.enqueue_chat(
        ChatAnswerPayload(
            message="Question scientifique immuable",
            conversation_id=conversation["id"],
            client_request_id=uuid4(),
            interaction_mode="research",
            evaluation_run_id="run-1",
            evaluation_question_id="Q1",
            evaluation_profile="p1",
        ),
        now=now,
    )
    profiles = []

    def fake_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
        experimental_profile,
    ) -> ChatbotResult:
        del active_settings, database, previous_sources
        assert history == []
        assert not use_external_sources
        assert interaction_mode == "research"
        profiles.append(experimental_profile)
        on_progress("generation")
        on_argo_reserved()
        on_argo_response()
        return ChatbotResult(
            message=message,
            retrieval_query=message,
            answer_markdown="Réponse évaluée",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
        )

    completed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {JobType.CHAT_ANSWER: ChatAnswerHandler(settings, repository.database, fake_answer)}
        ),
        worker_id="worker-evaluation",
        clock=lambda: now,
    ).run_once()

    assert completed is not None
    assert completed.state.value == "succeeded"
    assert profiles == ["p1"]
    persisted = repository.database.chat_conversation(str(enqueued.job.conversation_id))
    assert persisted is not None
    trace = persisted["messages"][-1]["response"]["evaluation"]
    assert trace["run_id"] == "run-1"
    assert trace["question_id"] == "Q1"
    assert trace["profile"] == "p1"
    assert len(trace["question_sha256"]) == 64


def test_evaluation_job_rejects_a_contaminated_conversation_before_generation(
    settings, tmp_path
) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    conversation = repository.database.create_chat_conversation("Evaluation Q1")
    enqueued = repository.enqueue_chat(
        ChatAnswerPayload(
            message="Question scientifique immuable",
            conversation_id=conversation["id"],
            client_request_id=uuid4(),
            interaction_mode="research",
            evaluation_run_id="run-1",
            evaluation_question_id="Q1",
            evaluation_profile="p0",
        ),
        now=now,
    )
    with repository.database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages(id, conversation_id, position, role, content, created_at)
            VALUES (?, ?, 1, 'user', 'Question différente', ?)
            """,
            (str(uuid4()), str(enqueued.job.conversation_id), now.isoformat()),
        )
    calls = 0

    def must_not_generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("generation must not start for a contaminated evaluation cell")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                JobType.CHAT_ANSWER: ChatAnswerHandler(
                    settings, repository.database, must_not_generate
                )
            }
        ),
        worker_id="worker-evaluation",
        clock=lambda: now,
    ).run_once()

    assert failed is not None
    assert failed.state.value == "failed"
    assert calls == 0
    persisted = repository.database.chat_conversation(str(enqueued.job.conversation_id))
    assert persisted is not None
    notice = persisted["messages"][-1]["response"]
    assert notice["diagnostic_code"] == "question_integrity"


def test_evaluation_retry_excludes_terminal_notice_from_generation_history(
    settings, tmp_path
) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    enqueued = repository.enqueue_evaluation_question(
        run_id="run-1",
        question_id="Q1",
        profile="p0",
        message="Question scientifique immuable",
        client_request_id=uuid4(),
        now=now,
    )

    class InvalidHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ArgoScientificValidationError("unsupported numeric claim")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: InvalidHandler()}),
        worker_id="worker-first",
        clock=lambda: now,
    ).run_once()
    assert failed is not None and failed.state.value == "failed"
    retried = repository.retry_failed(
        enqueued.job.id,
        client_request_id=uuid4(),
        now=now + timedelta(minutes=1),
    )
    assert retried is not None
    captured_history = []

    def fake_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
        experimental_profile,
    ) -> ChatbotResult:
        del (
            active_settings,
            database,
            use_external_sources,
            interaction_mode,
            previous_sources,
            experimental_profile,
            on_progress,
        )
        captured_history.extend(history)
        on_argo_reserved()
        on_argo_response()
        return ChatbotResult(
            message=message,
            retrieval_query=message,
            answer_markdown="Réponse après correction",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
        )

    completed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {JobType.CHAT_ANSWER: ChatAnswerHandler(settings, repository.database, fake_answer)}
        ),
        worker_id="worker-second",
        clock=lambda: now + timedelta(minutes=1),
    ).run_once()

    assert completed is not None and completed.state.value == "succeeded"
    assert captured_history == []


def test_chat_handler_reads_authoritative_history_from_sqlite(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id = uuid4()
    previous_user_id = uuid4()
    previous_assistant_id = uuid4()
    current_user_id = uuid4()
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO chat_conversations(id, title) VALUES (?, 'Test')",
            (str(conversation_id),),
        )
        connection.executemany(
            """
            INSERT INTO chat_messages(id, conversation_id, position, role, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (str(previous_user_id), str(conversation_id), 0, "user", "Question avant"),
                (
                    str(previous_assistant_id),
                    str(conversation_id),
                    1,
                    "assistant",
                    "Réponse avant",
                ),
                (str(current_user_id), str(conversation_id), 2, "user", "Question courante"),
            ],
        )
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question courante",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=current_user_id,
        now=now,
    )
    job = repository.claim_next(
        worker_id="worker-test", lease_duration=timedelta(minutes=5), now=now
    )
    assert job is not None
    captured_history = []

    def fake_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        del (
            active_settings,
            database,
            message,
            use_external_sources,
            on_argo_reserved,
            on_argo_response,
            on_progress,
        )
        captured_history.extend(history)
        return ChatbotResult(
            message="Question courante",
            retrieval_query="Question avant Question courante",
            answer_markdown="Réponse",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
        )

    handler = ChatAnswerHandler(settings, repository.database, fake_answer)
    handler.handle(
        job,
        JobProgressContext(
            repository=repository,
            job_id=job.id,
            worker_id="worker-test",
            clock=lambda: now,
        ),
    )

    assert captured_history == [
        {"role": "user", "content": "Question avant"},
        {"role": "assistant", "content": "Réponse avant"},
    ]


def test_chat_handler_reuses_persisted_sources_without_search_step(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation_id = uuid4()
    current_user_id = uuid4()
    response = {
        "sources": [
            {
                "record_id": "common:article-1",
                "origin": "local_rag",
                "evidence_level": "abstract",
                "scope": "common",
                "title": "Fermentation kinetics",
                "authors": ["Ada Test"],
                "doi": "10.1000/article-1",
                "journal": "Cider Science",
                "publication_year": 2025,
                "providers": ["corpus-base"],
                "url": "https://doi.org/10.1000/article-1",
                "snippet": "Fermentation kinetics depend on temperature.",
            }
        ]
    }
    with repository.database.transaction() as connection:
        connection.execute(
            "INSERT INTO chat_conversations(id, title) VALUES (?, 'Test')",
            (str(conversation_id),),
        )
        connection.executemany(
            """
            INSERT INTO chat_messages(
                id, conversation_id, position, role, content, response_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (str(uuid4()), str(conversation_id), 0, "user", "Question initiale", None),
                (
                    str(uuid4()),
                    str(conversation_id),
                    1,
                    "assistant",
                    "Réponse initiale",
                    json.dumps(response),
                ),
                (
                    str(current_user_id),
                    str(conversation_id),
                    2,
                    "user",
                    "Reformule en deux phrases",
                    None,
                ),
            ],
        )
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Reformule en deux phrases",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
            interaction_mode="conversation",
        ),
        user_message_id=current_user_id,
        now=now,
    )
    job = repository.claim_next(
        worker_id="worker-test",
        lease_duration=timedelta(minutes=5),
        now=now,
    )
    assert job is not None
    captured: list[object] = []

    def fake_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        del active_settings, database, message, history
        captured.extend([use_external_sources, interaction_mode, previous_sources])
        on_progress("planning")
        on_progress("generation")
        on_argo_reserved()
        on_argo_response()
        return ChatbotResult(
            message="Reformule en deux phrases",
            retrieval_query="Question initiale Reformule en deux phrases",
            answer_markdown="Réponse reformulée",
            sources=list(previous_sources),
            warnings=[],
            model="test-model",
            local_result_count=1,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
            interaction_mode="conversation",
            reused_previous_sources=True,
        )

    ChatAnswerHandler(settings, repository.database, fake_answer).handle(
        job,
        JobProgressContext(
            repository=repository,
            job_id=job.id,
            worker_id="worker-test",
            clock=lambda: now + timedelta(seconds=1),
        ),
    )

    assert captured[0:2] == [False, "conversation"]
    assert len(captured[2]) == 1
    with repository.database.connect() as connection:
        steps = [
            row["step"]
            for row in connection.execute(
                "SELECT step FROM job_events WHERE job_id = ?",
                (str(job.id),),
            )
        ]
    assert "search" not in steps


@pytest.mark.parametrize(
    ("profile_allows_enrichment", "expected_external", "expected_step"),
    [(False, False, JobStep.SEARCH), (True, True, JobStep.ENRICHMENT)],
)
def test_external_enrichment_requires_profile_authorization(
    settings,
    tmp_path,
    profile_allows_enrichment,
    expected_external,
    expected_step,
    monkeypatch,
) -> None:
    settings.app.allow_bibliographic_apis = profile_allows_enrichment
    settings.bibliographic.enabled = profile_allows_enrichment
    monkeypatch.setenv(
        LOCAL_PROFILE_ENV,
        "admin" if profile_allows_enrichment else "user",
    )
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now, use_external_sources=True)
    received_external = []

    def fake_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        del active_settings, database, history, on_argo_reserved, on_argo_response
        received_external.append(use_external_sources)
        on_progress("planning")
        on_progress("search")
        if use_external_sources:
            on_progress("enrichment")
        return ChatbotResult(
            message=message,
            retrieval_query=message,
            answer_markdown="Réponse",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
        )

    ChatAnswerHandler(settings, repository.database, fake_answer).handle(
        job,
        JobProgressContext(
            repository=repository,
            job_id=job.id,
            worker_id="worker-test",
            clock=lambda: now,
        ),
    )

    assert received_external == [expected_external]
    persisted = repository.get(job.id)
    assert persisted is not None
    assert persisted.step is expected_step


def test_validation_rejection_is_attributed_to_validation_step(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)

    def rejected_answer(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        del active_settings, database, message, history, use_external_sources
        on_progress("generation")
        on_argo_reserved()
        on_argo_response()
        raise RuntimeError("simulated scientific validation rejection")

    handler = ChatAnswerHandler(settings, repository.database, rejected_answer)
    with pytest.raises(RuntimeError, match="validation rejection"):
        handler.handle(
            job,
            JobProgressContext(
                repository=repository,
                job_id=job.id,
                worker_id="worker-test",
                clock=lambda: now + timedelta(seconds=1),
            ),
        )

    persisted = repository.get(job.id)
    assert persisted is not None
    assert persisted.step is JobStep.VALIDATION


def test_assistant_message_and_success_roll_back_together(tmp_path, monkeypatch) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)

    def fail_event(*args, **kwargs) -> None:
        raise RuntimeError("simulated success event failure")

    monkeypatch.setattr(repository, "_insert_event", fail_event)
    with pytest.raises(RuntimeError, match="success event failure"):
        repository.persist_result_and_succeed(
            job.id,
            worker_id="worker-test",
            assistant_content="Réponse non validée",
            assistant_response={"answer_markdown": "Réponse non validée"},
            response_time_milliseconds=10,
            now=now + timedelta(seconds=1),
        )

    persisted = repository.get(job.id)
    assert persisted is not None
    assert persisted.state.value == "running"
    assert persisted.result_message_id is None
    conversation = repository.database.chat_conversation(str(job.conversation_id))
    assert conversation is not None
    assert [message["role"] for message in conversation["messages"]] == ["user"]


def test_argo_timeout_uses_bounded_retry_delays(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    start = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, start)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class TimeoutHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ArgoUnavailableError("simulated timeout")

    current_time = [start]
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: TimeoutHandler()}),
        worker_id="worker-timeout",
        clock=lambda: current_time[0],
    )

    first = worker.run_once()
    assert first is not None
    assert first.state.value == "queued"
    assert first.available_at == start + timedelta(seconds=30)

    current_time[0] = first.available_at
    second = worker.run_once()
    assert second is not None
    assert second.state.value == "queued"
    assert second.available_at == current_time[0] + timedelta(minutes=2)

    current_time[0] = second.available_at
    third = worker.run_once()
    assert third is not None
    assert third.state.value == "failed"
    assert third.attempt == 3


def test_invalid_scientific_generation_is_terminal_without_exposing_detail(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class InvalidScientificAnswerHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ArgoScientificValidationError("unsupported numeric claim")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: InvalidScientificAnswerHandler()}),
        worker_id="worker-scientific-validation",
        clock=lambda: now,
    ).run_once()

    assert failed is not None
    assert failed.state.value == "failed"
    assert failed.attempt == 1
    assert failed.error_code.value == "validation"
    assert "arrêtée pour vérification" in failed.error_message
    assert "unsupported numeric claim" not in failed.error_message
    conversation = repository.database.chat_conversation(str(job.conversation_id))
    assert conversation is not None
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
    notice = conversation["messages"][-1]
    assert notice["response"] == {
        "kind": "job_terminal_notice",
        "job_id": str(job.id),
        "state": "failed",
        "error_code": "validation",
        "diagnostic_code": "unsupported_numeric_claim",
    }
    assert "unsupported numeric claim" not in notice["content"]


def test_unexpected_handler_bug_becomes_a_visible_terminal_diagnostic(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class BuggyHandler:
        def handle(self, job, context) -> JobHandlerResult:
            del job, context
            raise RuntimeError("sensitive implementation detail")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: BuggyHandler()}),
        worker_id="worker-bug",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert failed is not None
    assert failed.state is JobState.FAILED
    assert failed.error_code is JobErrorKind.VALIDATION
    assert "sensitive implementation detail" not in failed.error_message
    conversation = repository.database.chat_conversation(str(job.conversation_id))
    assert conversation is not None
    notice = conversation["messages"][-1]
    assert notice["role"] == "assistant"
    assert notice["response"]["diagnostic_code"] == "internal_handler_error"
    assert "sensitive implementation detail" not in notice["content"]


def test_unserializable_handler_result_becomes_a_visible_persistence_diagnostic(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class InvalidResultHandler:
        def handle(self, job, context) -> JobHandlerResult:
            del job, context
            return JobHandlerResult(
                assistant_content="Résultat non sérialisable",
                assistant_response={"invalid": object()},
                response_time_milliseconds=10,
            )

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: InvalidResultHandler()}),
        worker_id="worker-persistence",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert failed is not None
    assert failed.state is JobState.FAILED
    conversation = repository.database.chat_conversation(str(job.conversation_id))
    assert conversation is not None
    assert conversation["messages"][-1]["response"]["diagnostic_code"] == (
        "internal_persistence_error"
    )


def test_argo_authentication_failure_is_terminal_and_actionable(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class AuthenticationHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ArgoAuthenticationError("secret provider detail")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: AuthenticationHandler()}),
        worker_id="worker-auth",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert failed is not None
    assert failed.state.value == "failed"
    assert failed.attempt == 1
    assert failed.error_code.value == "authentication"
    assert "remplacée" in failed.error_message
    assert "secret provider detail" not in failed.error_message


def test_argo_authorization_failure_does_not_request_key_replacement(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class AuthorizationHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ArgoAuthorizationError("provider policy detail")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: AuthorizationHandler()}),
        worker_id="worker-authorization",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert failed is not None
    assert failed.state.value == "failed"
    assert failed.error_code.value == "validation"
    assert "opération ou ce modèle" in failed.error_message
    assert "remplac" not in failed.error_message
    assert "provider policy detail" not in failed.error_message


def test_no_sources_failure_is_terminal_without_stopping_the_worker(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class NoSourcesHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ChatbotNoSourcesError("no eligible abstract")

    failed = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: NoSourcesHandler()}),
        worker_id="worker-no-sources",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert failed is not None
    assert failed.state.value == "failed"
    assert failed.attempt == 1
    assert failed.error_code.value == "validation"
    assert failed.error_message == (
        "Aucune source scientifique qualifiée n'est disponible pour répondre à cette question."
    )


def test_local_quota_keeps_job_queued_until_persisted_retry_time(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )
    retry_at = now + timedelta(minutes=10)

    class QuotaThenSuccessHandler:
        calls = 0

        def handle(self, job, context) -> JobHandlerResult:
            self.calls += 1
            if self.calls == 1:
                raise ArgoLocalQuotaError(retry_at)
            return JobHandlerResult(
                assistant_content="Réponse après quota",
                assistant_response={"answer_markdown": "Réponse après quota"},
                response_time_milliseconds=10,
            )

    handler = QuotaThenSuccessHandler()

    deferred = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: handler}),
        worker_id="worker-quota",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert deferred is not None
    assert deferred.state.value == "queued"
    assert deferred.attempt == 0
    assert deferred.available_at == retry_at
    assert deferred.error_code.value == "quota"

    succeeded = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: handler}),
        worker_id="worker-after-quota",
        clock=lambda: retry_at,
    ).run_once()

    assert succeeded is not None
    assert succeeded.state.value == "succeeded"
    assert succeeded.attempt == 1
    assert succeeded.error_code is None
    assert succeeded.result_message_id is not None
    assert handler.calls == 2


def test_remote_quota_defers_without_stopping_or_consuming_an_attempt(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class RemoteQuotaHandler:
        def handle(self, job, context) -> JobHandlerResult:
            raise ArgoQuotaError("provider detail")

    deferred_at = now + timedelta(seconds=1)
    deferred = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: RemoteQuotaHandler()}),
        worker_id="worker-remote-quota",
        clock=lambda: deferred_at,
    ).run_once()

    assert deferred is not None
    assert deferred.state.value == "queued"
    assert deferred.attempt == 0
    assert deferred.error_code.value == "quota"
    assert deferred.available_at == deferred_at + timedelta(minutes=1)
    assert "provider detail" not in deferred.error_message


def test_cancellation_before_argo_sends_no_argo_request(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )
    argo_requests = 0

    def cancelled_before_argo(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        nonlocal argo_requests
        del active_settings, database, message, history, use_external_sources, on_argo_response
        on_progress("planning")
        repository.request_cancellation(job.id, now=now + timedelta(seconds=1))
        on_argo_reserved()
        argo_requests += 1
        raise AssertionError("ARGO HTTP request must not start")

    cancelled = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                JobType.CHAT_ANSWER: ChatAnswerHandler(
                    settings, repository.database, cancelled_before_argo
                )
            }
        ),
        worker_id="worker-test",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert cancelled is not None
    assert cancelled.state.value == "cancelled"
    assert argo_requests == 0


def test_cancellation_after_non_interruptible_argo_call_is_honest(settings, tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )
    argo_requests = 0

    def cancelled_after_argo(
        active_settings,
        database,
        *,
        message,
        history,
        use_external_sources,
        interaction_mode,
        previous_sources,
        on_argo_reserved,
        on_argo_response,
        on_progress,
    ) -> ChatbotResult:
        nonlocal argo_requests
        del active_settings, database, history, use_external_sources
        on_progress("generation")
        on_argo_reserved()
        argo_requests += 1
        repository.request_cancellation(job.id, now=now + timedelta(seconds=1))
        on_argo_response()
        return ChatbotResult(
            message=message,
            retrieval_query=message,
            answer_markdown="Réponse à jeter",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
        )

    cancelled = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(
            {
                JobType.CHAT_ANSWER: ChatAnswerHandler(
                    settings, repository.database, cancelled_after_argo
                )
            }
        ),
        worker_id="worker-test",
        clock=lambda: now + timedelta(seconds=1),
    ).run_once()

    assert cancelled is not None
    assert cancelled.state.value == "cancelled"
    assert argo_requests == 1
    conversation = repository.database.chat_conversation(str(job.conversation_id))
    assert conversation is not None
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
    assert conversation["messages"][-1]["response"]["kind"] == "job_terminal_notice"
    assert conversation["messages"][-1]["response"]["state"] == "cancelled"


def test_worker_logs_only_structured_ids_steps_and_durations(tmp_path, caplog) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    job = _claimed_job(repository, now)
    with repository.database.transaction() as connection:
        connection.execute(
            """
            UPDATE jobs
            SET state = 'queued', attempt = 0, worker_id = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ?
            """,
            (str(job.id),),
        )

    class SensitiveHandler:
        def handle(self, job, context) -> JobHandlerResult:
            return JobHandlerResult(
                assistant_content="SCIENTIFIC_CONTENT_SENTINEL",
                assistant_response={"answer_markdown": "SCIENTIFIC_CONTENT_SENTINEL"},
                response_time_milliseconds=10,
            )

    monotonic_values = iter((10.0, 10.25))
    with caplog.at_level(logging.INFO, logger="ciderscholar.jobs.worker"):
        completed = DurableJobWorker(
            repository=repository,
            registry=JobHandlerRegistry({JobType.CHAT_ANSWER: SensitiveHandler()}),
            worker_id="worker-log",
            clock=lambda: now + timedelta(seconds=1),
            monotonic_clock=lambda: next(monotonic_values),
        ).run_once()

    assert completed is not None
    records = [record for record in caplog.records if record.msg == "job_finished"]
    assert len(records) == 1
    record = records[0]
    assert record.job_id == str(job.id)
    assert record.job_step == "persistence"
    assert record.duration_milliseconds == 250
    assert "SCIENTIFIC_CONTENT_SENTINEL" not in caplog.text
    assert "Question durable" not in caplog.text
