"""SQLite-backed durable queue, independent from the HTTP framework."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.jobs.contracts import (
    ACTIVE_JOB_STATES,
    JOB_ERROR_DISPOSITIONS,
    JOB_STEP_ORDER,
    MAX_JOB_ATTEMPTS,
    ChatAnswerPayload,
    CorpusIngestionPayload,
    DeepResearchPayload,
    JobErrorDisposition,
    JobErrorKind,
    JobPayload,
    JobPublic,
    JobPublicError,
    JobState,
    JobStep,
    JobType,
    LongSynthesisPayload,
    WeeklyMaintenancePayload,
    retry_delay_after,
)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("job timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


MAX_ACTIVE_JOBS_PER_CONVERSATION = 3
MAINTENANCE_CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000001")
MAINTENANCE_MESSAGE_ID = UUID("00000000-0000-4000-8000-000000000002")
LONG_SYNTHESIS_CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000003")
CORPUS_INGESTION_CONVERSATION_ID = UUID("00000000-0000-4000-8000-000000000004")


class ActiveJobLimitError(RuntimeError):
    def __init__(self, limit: int = MAX_ACTIVE_JOBS_PER_CONVERSATION) -> None:
        self.limit = limit
        super().__init__(f"conversation already has {limit} active jobs")


class EvaluationConversationIsolationError(RuntimeError):
    """An evaluation cell attempted to reuse a non-empty conversation."""


class EvaluationQuestionAlreadySubmittedError(RuntimeError):
    """The immutable run/profile/question cell already exists."""


class EvaluationRunBusyError(RuntimeError):
    """Another durable job is active while an evaluation cell is submitted."""


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Internal durable job projection used by workers, never serialized directly."""

    id: UUID
    type: JobType
    state: JobState
    step: JobStep
    payload: JobPayload
    priority: int
    attempt: int
    available_at: datetime
    worker_id: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    conversation_id: UUID
    user_message_id: UUID
    result_message_id: UUID | None
    client_request_id: UUID
    error_code: JobErrorKind | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    def to_public(self) -> JobPublic:
        """Return the safe API projection, excluding payload and worker metadata."""

        error = None
        if self.error_code is not None and self.error_message is not None:
            error = JobPublicError(
                code=self.error_code,
                message=self.error_message,
                retry_at=(self.available_at if self.state is JobState.QUEUED else None),
            )
        return JobPublic(
            id=self.id,
            conversation_id=self.conversation_id,
            type=self.type,
            state=self.state,
            step=self.step,
            attempt=self.attempt,
            available_at=self.available_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result_message_id=self.result_message_id,
            error=error,
        )


@dataclass(frozen=True, slots=True)
class LeaseRecoverySummary:
    """Deterministic outcome of one expired-lease recovery pass."""

    requeued: tuple[UUID, ...]
    failed: tuple[UUID, ...]
    cancelled: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    """Content-free queue pressure projection for operators and demonstrations."""

    depth: int
    queued: int
    running: int
    cancel_requested: int
    oldest_created_at: datetime | None
    oldest_age_seconds: int | None


@dataclass(frozen=True, slots=True)
class ActiveJobDiagnostic:
    """Content-free projection used by local runtime diagnostics."""

    id: UUID
    type: JobType
    state: JobState
    step: JobStep
    created_at: datetime
    heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class EnqueuedChat:
    """Atomic chat submission result, including its canonical user message."""

    job: JobRecord
    user_message_id: UUID
    user_message_content: str
    user_message_created_at: datetime
    created: bool


class JobRepository:
    """Own durable queue persistence for one local SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.database = Database(self.path)

    def initialize(self) -> None:
        """Create or migrate the database before queue operations."""

        self.database.initialize()

    @staticmethod
    def _assert_evaluation_queue_idle(connection: sqlite3.Connection) -> None:
        active = connection.execute(
            """
            SELECT id FROM jobs
            WHERE state IN (?, ?, ?)
            LIMIT 1
            """,
            (
                JobState.QUEUED.value,
                JobState.RUNNING.value,
                JobState.CANCEL_REQUESTED.value,
            ),
        ).fetchone()
        if active is not None:
            raise EvaluationRunBusyError(
                "an evaluation submission requires an otherwise idle durable queue"
            )

    @staticmethod
    def _assert_evaluation_submission(
        connection: sqlite3.Connection,
        payload: ChatAnswerPayload,
    ) -> None:
        if payload.evaluation_run_id is None:
            return
        JobRepository._assert_evaluation_queue_idle(connection)
        duplicate = connection.execute(
            """
            SELECT id FROM jobs
            WHERE type = ?
              AND json_extract(payload_json, '$.evaluation_run_id') = ?
              AND json_extract(payload_json, '$.evaluation_question_id') = ?
              AND json_extract(payload_json, '$.evaluation_profile') = ?
            LIMIT 1
            """,
            (
                JobType.CHAT_ANSWER.value,
                payload.evaluation_run_id,
                payload.evaluation_question_id,
                payload.evaluation_profile,
            ),
        ).fetchone()
        if duplicate is not None:
            raise EvaluationQuestionAlreadySubmittedError(
                "this evaluation run/profile/question cell has already been submitted"
            )
        conversation_use = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM chat_messages WHERE conversation_id = ?) AS messages,
              (SELECT COUNT(*) FROM jobs WHERE conversation_id = ?) AS jobs
            """,
            (str(payload.conversation_id), str(payload.conversation_id)),
        ).fetchone()
        if conversation_use is None or conversation_use["messages"] or conversation_use["jobs"]:
            raise EvaluationConversationIsolationError(
                "each evaluation question requires a fresh empty conversation"
            )

    @staticmethod
    def _assert_evaluation_retry(
        connection: sqlite3.Connection,
        payload: ChatAnswerPayload,
        user_message_id: UUID,
    ) -> None:
        if payload.evaluation_run_id is None:
            return
        JobRepository._assert_evaluation_queue_idle(connection)
        failed_cell = connection.execute(
            """
            SELECT id FROM jobs
            WHERE type = ? AND state = ? AND conversation_id = ? AND user_message_id = ?
              AND json_extract(payload_json, '$.evaluation_run_id') = ?
              AND json_extract(payload_json, '$.evaluation_question_id') = ?
              AND json_extract(payload_json, '$.evaluation_profile') = ?
            LIMIT 1
            """,
            (
                JobType.CHAT_ANSWER.value,
                JobState.FAILED.value,
                str(payload.conversation_id),
                str(user_message_id),
                payload.evaluation_run_id,
                payload.evaluation_question_id,
                payload.evaluation_profile,
            ),
        ).fetchone()
        if failed_cell is None:
            raise EvaluationQuestionAlreadySubmittedError(
                "an evaluation retry requires the failed immutable cell"
            )
        user_messages = connection.execute(
            """
            SELECT id, content FROM chat_messages
            WHERE conversation_id = ? AND role = 'user'
            ORDER BY position
            """,
            (str(payload.conversation_id),),
        ).fetchall()
        if (
            len(user_messages) != 1
            or user_messages[0]["id"] != str(user_message_id)
            or user_messages[0]["content"] != payload.message
        ):
            raise EvaluationConversationIsolationError(
                "an evaluation retry cannot change or add a conversation question"
            )

    @staticmethod
    def _persist_terminal_notice(
        connection: sqlite3.Connection,
        *,
        job_id: UUID,
        conversation_id: str,
        state: JobState,
        content: str,
        created_at: str,
        error_code: str | None = None,
        diagnostic_code: str | None = None,
    ) -> UUID:
        """Persist a visible, machine-identifiable terminal outcome for a chat question."""

        result_message_id = uuid4()
        position = connection.execute(
            """
            SELECT COALESCE(MAX(position), -1) + 1
            FROM chat_messages WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()[0]
        response = {
            "kind": "job_terminal_notice",
            "job_id": str(job_id),
            "state": state.value,
            "error_code": error_code,
            "diagnostic_code": diagnostic_code,
        }
        connection.execute(
            """
            INSERT INTO chat_messages(
                id, conversation_id, position, role, content,
                response_json, response_time_milliseconds, created_at
            ) VALUES (?, ?, ?, 'assistant', ?, ?, NULL, ?)
            """,
            (
                str(result_message_id),
                conversation_id,
                position,
                content,
                json.dumps(response, ensure_ascii=False),
                created_at,
            ),
        )
        connection.execute(
            "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
            (created_at, conversation_id),
        )
        return result_message_id

    def active_job_count(self) -> int:
        """Count all durable work that must block an application replacement."""

        if not self.path.is_file():
            return 0
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM jobs
                WHERE state IN ('queued', 'running', 'cancel_requested')
                """
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def queue_metrics(self, *, now: datetime | None = None) -> QueueMetrics:
        """Return active counts and oldest age without payload or conversation content."""

        measured_at = now or datetime.now(UTC)
        if measured_at.tzinfo is None or measured_at.utcoffset() is None:
            raise ValueError("queue metric timestamp must be timezone-aware")
        if not self.path.is_file():
            return QueueMetrics(0, 0, 0, 0, None, None)
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS depth,
                       SUM(CASE WHEN state = 'queued' THEN 1 ELSE 0 END) AS queued,
                       SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running,
                       SUM(CASE WHEN state = 'cancel_requested' THEN 1 ELSE 0 END)
                         AS cancel_requested,
                       MIN(created_at) AS oldest_created_at
                FROM jobs
                WHERE state IN ('queued', 'running', 'cancel_requested')
                """
            ).fetchone()
        oldest = _parse_timestamp(row[4]) if row and row[4] else None
        if oldest is not None and (oldest.tzinfo is None or oldest.utcoffset() is None):
            oldest = oldest.replace(tzinfo=UTC)
        age = max(0, int((measured_at - oldest).total_seconds())) if oldest else None
        values = row or (0, 0, 0, 0, None)
        return QueueMetrics(
            depth=int(values[0] or 0),
            queued=int(values[1] or 0),
            running=int(values[2] or 0),
            cancel_requested=int(values[3] or 0),
            oldest_created_at=oldest,
            oldest_age_seconds=age,
        )

    def enqueue(
        self,
        payload: JobPayload,
        *,
        user_message_id: UUID,
        priority: int = 100,
        now: datetime | None = None,
        available_at: datetime | None = None,
        enforce_active_limit: bool = False,
    ) -> JobRecord:
        """Persist a queued chat job and its initial event in one transaction."""

        queued_at = now or datetime.now(UTC)
        queued_timestamp = _timestamp(queued_at)
        available_timestamp = (
            _timestamp(available_at) if available_at is not None else queued_timestamp
        )
        job_id = uuid4()
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE conversation_id = ? AND client_request_id = ?
                """,
                (str(payload.conversation_id), str(payload.client_request_id)),
            ).fetchone()
            if existing is not None:
                return self._row_to_record(existing)
            if isinstance(payload, ChatAnswerPayload):
                self._assert_evaluation_retry(connection, payload, user_message_id)
            if enforce_active_limit:
                self._assert_active_limit(connection, payload.conversation_id)
            row = self._insert_queued_job(
                connection,
                job_id=job_id,
                payload=payload,
                user_message_id=user_message_id,
                priority=priority,
                available_at=available_timestamp,
                created_at=queued_timestamp,
                job_type=self._job_type_for_payload(payload),
            )
        if row is None:
            raise RuntimeError("queued job disappeared before commit")
        return self._row_to_record(row)

    def enqueue_chat(
        self,
        payload: ChatAnswerPayload | DeepResearchPayload,
        *,
        priority: int = 100,
        now: datetime | None = None,
    ) -> EnqueuedChat:
        """Atomically persist the user message, job, and first event."""

        queued_at = now or datetime.now(UTC)
        queued_timestamp = _timestamp(queued_at)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE conversation_id = ? AND client_request_id = ?
                """,
                (str(payload.conversation_id), str(payload.client_request_id)),
            ).fetchone()
            if existing is not None:
                message = connection.execute(
                    """
                    SELECT id, content, created_at FROM chat_messages
                    WHERE id = ? AND role = 'user'
                    """,
                    (existing["user_message_id"],),
                ).fetchone()
                if message is None:
                    raise RuntimeError("idempotent job has no persisted user message")
                return EnqueuedChat(
                    job=self._row_to_record(existing),
                    user_message_id=UUID(message["id"]),
                    user_message_content=message["content"],
                    user_message_created_at=datetime.fromisoformat(message["created_at"]),
                    created=False,
                )
            if isinstance(payload, ChatAnswerPayload):
                self._assert_evaluation_submission(connection, payload)
            self._assert_active_limit(connection, payload.conversation_id)
            conversation = connection.execute(
                "SELECT id FROM chat_conversations WHERE id = ?",
                (str(payload.conversation_id),),
            ).fetchone()
            if conversation is None:
                raise ValueError("chat conversation does not exist")
            position = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM chat_messages WHERE conversation_id = ?
                """,
                (str(payload.conversation_id),),
            ).fetchone()[0]
            user_message_id = uuid4()
            connection.execute(
                """
                INSERT INTO chat_messages(
                    id, conversation_id, position, role, content, created_at
                ) VALUES (?, ?, ?, 'user', ?, ?)
                """,
                (
                    str(user_message_id),
                    str(payload.conversation_id),
                    position,
                    payload.message,
                    queued_timestamp,
                ),
            )
            row = self._insert_queued_job(
                connection,
                job_id=uuid4(),
                payload=payload,
                user_message_id=user_message_id,
                priority=priority,
                available_at=queued_timestamp,
                created_at=queued_timestamp,
                job_type=(
                    JobType.DEEP_RESEARCH
                    if isinstance(payload, DeepResearchPayload)
                    else JobType.CHAT_ANSWER
                ),
            )
            connection.execute(
                "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
                (queued_timestamp, str(payload.conversation_id)),
            )
        return EnqueuedChat(
            job=self._row_to_record(row),
            user_message_id=user_message_id,
            user_message_content=payload.message,
            user_message_created_at=queued_at.astimezone(UTC),
            created=True,
        )

    def enqueue_evaluation_question(
        self,
        *,
        run_id: str,
        question_id: str,
        profile: str,
        message: str,
        client_request_id: UUID,
        priority: int = 100,
        now: datetime | None = None,
    ) -> EnqueuedChat:
        """Atomically create one isolated conversation and its immutable evaluation cell."""

        queued_at = now or datetime.now(UTC)
        queued_timestamp = _timestamp(queued_at)
        conversation_id = uuid4()
        payload = ChatAnswerPayload(
            message=message,
            conversation_id=conversation_id,
            client_request_id=client_request_id,
            use_external_sources=False,
            analyze_figures=False,
            interaction_mode="research",
            evaluation_run_id=run_id,
            evaluation_question_id=question_id,
            evaluation_profile=profile,
        )
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE type = ?
                  AND json_extract(payload_json, '$.evaluation_run_id') = ?
                  AND json_extract(payload_json, '$.evaluation_question_id') = ?
                  AND json_extract(payload_json, '$.evaluation_profile') = ?
                LIMIT 1
                """,
                (JobType.CHAT_ANSWER.value, run_id, question_id, profile),
            ).fetchone()
            if existing is not None:
                if existing["client_request_id"] != str(client_request_id):
                    raise EvaluationQuestionAlreadySubmittedError(
                        "this evaluation run/profile/question cell has already been submitted"
                    )
                user_message = connection.execute(
                    """
                    SELECT id, content, created_at FROM chat_messages
                    WHERE id = ? AND role = 'user'
                    """,
                    (existing["user_message_id"],),
                ).fetchone()
                if user_message is None:
                    raise RuntimeError("idempotent evaluation job has no persisted user message")
                return EnqueuedChat(
                    job=self._row_to_record(existing),
                    user_message_id=UUID(user_message["id"]),
                    user_message_content=user_message["content"],
                    user_message_created_at=datetime.fromisoformat(user_message["created_at"]),
                    created=False,
                )
            self._assert_evaluation_submission(connection, payload)
            title = f"[{profile.upper()} {question_id}] {' '.join(message.split())}"[:120]
            connection.execute(
                """
                INSERT INTO chat_conversations(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(conversation_id), title, queued_timestamp, queued_timestamp),
            )
            user_message_id = uuid4()
            connection.execute(
                """
                INSERT INTO chat_messages(
                    id, conversation_id, position, role, content, created_at
                ) VALUES (?, ?, 0, 'user', ?, ?)
                """,
                (
                    str(user_message_id),
                    str(conversation_id),
                    payload.message,
                    queued_timestamp,
                ),
            )
            row = self._insert_queued_job(
                connection,
                job_id=uuid4(),
                payload=payload,
                user_message_id=user_message_id,
                priority=priority,
                available_at=queued_timestamp,
                created_at=queued_timestamp,
                job_type=JobType.CHAT_ANSWER,
            )
        return EnqueuedChat(
            job=self._row_to_record(row),
            user_message_id=user_message_id,
            user_message_content=payload.message,
            user_message_created_at=queued_at.astimezone(UTC),
            created=True,
        )

    def enqueue_deep_research(
        self,
        payload: DeepResearchPayload,
        *,
        priority: int = 110,
        now: datetime | None = None,
    ) -> EnqueuedChat:
        """Persist one deep-research question with the same idempotency boundary as chat."""

        return self.enqueue_chat(payload, priority=priority, now=now)

    def enqueue_weekly_maintenance(
        self,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        """Enqueue at most one persistent maintenance job across all processes."""

        queued_at = now or datetime.now(UTC)
        timestamp = _timestamp(queued_at)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE type = ? AND state IN ('queued', 'running', 'cancel_requested')
                ORDER BY created_at LIMIT 1
                """,
                (JobType.WEEKLY_MAINTENANCE.value,),
            ).fetchone()
            if existing is not None:
                return self._row_to_record(existing)
            connection.execute(
                """
                INSERT INTO chat_conversations(id, title, created_at, updated_at)
                VALUES (?, 'Maintenance hebdomadaire', ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (str(MAINTENANCE_CONVERSATION_ID), timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO chat_messages(
                    id, conversation_id, position, role, content, created_at
                ) VALUES (?, ?, 0, 'user', 'Maintenance hebdomadaire', ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (str(MAINTENANCE_MESSAGE_ID), str(MAINTENANCE_CONVERSATION_ID), timestamp),
            )
            maintenance_id = uuid4()
            payload = WeeklyMaintenancePayload(
                maintenance_id=maintenance_id,
                conversation_id=MAINTENANCE_CONVERSATION_ID,
                client_request_id=maintenance_id,
                requested_at=queued_at,
            )
            row = self._insert_queued_job(
                connection,
                job_id=uuid4(),
                job_type=JobType.WEEKLY_MAINTENANCE,
                payload=payload,
                user_message_id=MAINTENANCE_MESSAGE_ID,
                priority=20,
                available_at=timestamp,
                created_at=timestamp,
            )
        return self._row_to_record(row)

    def enqueue_long_synthesis(
        self,
        payload: LongSynthesisPayload,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        """Queue a resumable synthesis without performing ARGO work in the API process."""

        return self._enqueue_background(
            payload,
            job_type=JobType.LONG_SYNTHESIS,
            title="Synthèses longues",
            user_message=f"Synthèse longue de l'analyse {payload.query_id}",
            priority=105,
            now=now,
        )

    def enqueue_corpus_ingestion(
        self,
        payload: CorpusIngestionPayload,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        """Queue corpus PDFs by staged file name without exposing their content."""

        return self._enqueue_background(
            payload,
            job_type=JobType.CORPUS_INGESTION,
            title="Ingestions de documents",
            user_message=f"Ingestion de {len(payload.staged_files)} document(s)",
            priority=90,
            now=now,
        )

    def _enqueue_background(
        self,
        payload: LongSynthesisPayload | CorpusIngestionPayload,
        *,
        job_type: JobType,
        title: str,
        user_message: str,
        priority: int,
        now: datetime | None,
    ) -> JobRecord:
        queued_at = now or datetime.now(UTC)
        timestamp = _timestamp(queued_at)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE conversation_id = ? AND client_request_id = ?
                """,
                (str(payload.conversation_id), str(payload.client_request_id)),
            ).fetchone()
            if existing is not None:
                return self._row_to_record(existing)
            connection.execute(
                """
                INSERT INTO chat_conversations(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (str(payload.conversation_id), title, timestamp, timestamp),
            )
            position = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM chat_messages WHERE conversation_id = ?
                """,
                (str(payload.conversation_id),),
            ).fetchone()[0]
            user_message_id = uuid4()
            connection.execute(
                """
                INSERT INTO chat_messages(
                    id, conversation_id, position, role, content, created_at
                ) VALUES (?, ?, ?, 'user', ?, ?)
                """,
                (
                    str(user_message_id),
                    str(payload.conversation_id),
                    position,
                    user_message,
                    timestamp,
                ),
            )
            row = self._insert_queued_job(
                connection,
                job_id=uuid4(),
                job_type=job_type,
                payload=payload,
                user_message_id=user_message_id,
                priority=priority,
                available_at=timestamp,
                created_at=timestamp,
            )
            connection.execute(
                "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
                (timestamp, str(payload.conversation_id)),
            )
        return self._row_to_record(row)

    def get(self, job_id: UUID) -> JobRecord | None:
        """Return one internal job by ID, or None when it does not exist."""

        with closing(self.database.connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def retry_failed(
        self,
        job_id: UUID,
        *,
        client_request_id: UUID,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Create or recover an idempotent new attempt for one terminal failure."""

        original = self.get(job_id)
        if original is None or original.state is not JobState.FAILED:
            return None
        payload = original.payload.model_copy(update={"client_request_id": client_request_id})
        return self.enqueue(
            payload,
            user_message_id=original.user_message_id,
            priority=original.priority,
            now=now,
            enforce_active_limit=True,
        )

    def list_active(self, conversation_id: UUID) -> list[JobRecord]:
        """List only non-terminal jobs attached to a conversation."""

        states = tuple(state.value for state in ACTIVE_JOB_STATES)
        placeholders = ", ".join("?" for _ in states)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE conversation_id = ? AND state IN ({placeholders})
                ORDER BY created_at, id
                """,
                (str(conversation_id), *states),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active_diagnostics(self, *, limit: int = 100) -> list[ActiveJobDiagnostic]:
        """List bounded active-work metadata without loading job payloads."""

        if not 1 <= limit <= 100:
            raise ValueError("diagnostic limit must be between 1 and 100")
        states = tuple(state.value for state in ACTIVE_JOB_STATES)
        placeholders = ", ".join("?" for _ in states)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT id, type, state, step, created_at, heartbeat_at
                FROM jobs
                WHERE state IN ({placeholders})
                ORDER BY created_at, id
                LIMIT ?
                """,
                (*states, limit),
            ).fetchall()
        return [
            ActiveJobDiagnostic(
                id=UUID(row["id"]),
                type=JobType(row["type"]),
                state=JobState(row["state"]),
                step=JobStep(row["step"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                heartbeat_at=_parse_timestamp(row["heartbeat_at"]),
            )
            for row in rows
        ]

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        job_types: Sequence[JobType] | None = None,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Atomically lease the next available job in priority/FIFO order."""

        owner = worker_id.strip()
        if not owner or len(owner) > 200:
            raise ValueError("worker_id must contain between 1 and 200 characters")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        claimed_at = now or datetime.now(UTC)
        claimed_timestamp = _timestamp(claimed_at)
        lease_expires_at = _timestamp(claimed_at + lease_duration)
        accepted_types = (
            tuple(dict.fromkeys(JobType(job_type).value for job_type in job_types))
            if job_types is not None
            else ()
        )
        if job_types is not None and not accepted_types:
            raise ValueError("job_types must contain at least one accepted job type")
        type_filter = (
            f"AND type IN ({', '.join('?' for _ in accepted_types)})" if accepted_types else ""
        )

        with self.database.transaction() as connection:
            candidate = connection.execute(
                f"""
                SELECT id FROM jobs
                WHERE state = ? AND available_at <= ? AND attempt < ?
                  {type_filter}
                ORDER BY priority, available_at, created_at, id
                LIMIT 1
                """,
                (
                    JobState.QUEUED.value,
                    claimed_timestamp,
                    MAX_JOB_ATTEMPTS,
                    *accepted_types,
                ),
            ).fetchone()
            if candidate is None:
                return None
            cursor = connection.execute(
                """
                UPDATE jobs
                SET state = ?, step = ?, attempt = attempt + 1,
                    worker_id = ?, lease_expires_at = ?, heartbeat_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?,
                    error_code = NULL, error_message = NULL
                WHERE id = ? AND state = ? AND available_at <= ?
                """,
                (
                    JobState.RUNNING.value,
                    JobStep.WAITING.value,
                    owner,
                    lease_expires_at,
                    claimed_timestamp,
                    claimed_timestamp,
                    claimed_timestamp,
                    candidate["id"],
                    JobState.QUEUED.value,
                    claimed_timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("job claim lost while holding the queue transaction")
            self._insert_event(
                connection,
                job_id=UUID(candidate["id"]),
                state=JobState.RUNNING,
                step=JobStep.WAITING,
                technical_message="job.claimed",
                created_at=claimed_timestamp,
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (candidate["id"],)
            ).fetchone()
        if row is None:
            raise RuntimeError("claimed job disappeared before commit")
        return self._row_to_record(row)

    def heartbeat(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Renew an unexpired lease only when the caller still owns it."""

        owner = worker_id.strip()
        if not owner or len(owner) > 200:
            raise ValueError("worker_id must contain between 1 and 200 characters")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        heartbeat_at = now or datetime.now(UTC)
        heartbeat_timestamp = _timestamp(heartbeat_at)
        lease_expires_at = _timestamp(heartbeat_at + lease_duration)

        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND worker_id = ?
                  AND state IN (?, ?)
                  AND lease_expires_at >= ?
                """,
                (
                    heartbeat_timestamp,
                    lease_expires_at,
                    heartbeat_timestamp,
                    str(job_id),
                    owner,
                    JobState.RUNNING.value,
                    JobState.CANCEL_REQUESTED.value,
                    heartbeat_timestamp,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def transition_step(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        step: JobStep,
        technical_message: str | None = None,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Advance one owned running job and append its event atomically."""

        transition_at = now or datetime.now(UTC)
        transition_timestamp = _timestamp(transition_at)
        message = (technical_message or f"job.step.{step.value}").strip()
        if not 1 <= len(message) <= 300:
            raise ValueError("technical_message must contain between 1 and 300 characters")

        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT state, step FROM jobs
                WHERE id = ? AND worker_id = ? AND state = ?
                  AND lease_expires_at >= ?
                """,
                (
                    str(job_id),
                    worker_id,
                    JobState.RUNNING.value,
                    transition_timestamp,
                ),
            ).fetchone()
            if current is None:
                return None
            current_step = JobStep(current["step"])
            if JOB_STEP_ORDER[step] <= JOB_STEP_ORDER[current_step]:
                raise ValueError(f"job step cannot move from {current_step.value} to {step.value}")
            connection.execute(
                "UPDATE jobs SET step = ?, updated_at = ? WHERE id = ?",
                (step.value, transition_timestamp, str(job_id)),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                state=JobState.RUNNING,
                step=step,
                technical_message=message,
                created_at=transition_timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def rewind_maintenance_after_rollback(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Reset only a leased maintenance step after its verified corpus rollback."""

        timestamp = _timestamp(now or datetime.now(UTC))
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET step = ?, updated_at = ?
                WHERE id = ? AND type = ? AND state = ? AND worker_id = ?
                """,
                (
                    JobStep.BACKUP.value,
                    timestamp,
                    str(job_id),
                    JobType.WEEKLY_MAINTENANCE.value,
                    JobState.RUNNING.value,
                    worker_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._insert_event(
                connection,
                job_id=job_id,
                state=JobState.RUNNING,
                step=JobStep.BACKUP,
                technical_message="maintenance.rollback_completed",
                created_at=timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def succeed(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        result_message_id: UUID,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Complete an owned job only when its assistant result is persisted."""

        completed_at = now or datetime.now(UTC)
        completed_timestamp = _timestamp(completed_at)
        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT job.conversation_id
                FROM jobs AS job
                JOIN chat_messages AS message
                  ON message.id = ?
                 AND message.conversation_id = job.conversation_id
                 AND message.role = 'assistant'
                WHERE job.id = ? AND job.worker_id = ? AND job.state = ?
                  AND job.lease_expires_at >= ?
                """,
                (
                    str(result_message_id),
                    str(job_id),
                    worker_id,
                    JobState.RUNNING.value,
                    completed_timestamp,
                ),
            ).fetchone()
            if current is None:
                return None
            row = self._mark_succeeded(
                connection,
                job_id=job_id,
                result_message_id=result_message_id,
                completed_at=completed_timestamp,
            )
        return self._row_to_record(row)

    def persist_result_and_succeed(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        assistant_content: str,
        assistant_response: dict[str, Any],
        response_time_milliseconds: float,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Persist the assistant message and job success in one transaction."""

        content = assistant_content.strip()
        if not content:
            raise ValueError("assistant_content cannot be empty")
        if response_time_milliseconds < 0:
            raise ValueError("response_time_milliseconds cannot be negative")
        completed_at = now or datetime.now(UTC)
        completed_timestamp = _timestamp(completed_at)
        result_message_id = uuid4()

        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT conversation_id FROM jobs
                WHERE id = ? AND worker_id = ? AND state = ?
                  AND lease_expires_at >= ?
                """,
                (
                    str(job_id),
                    worker_id,
                    JobState.RUNNING.value,
                    completed_timestamp,
                ),
            ).fetchone()
            if current is None:
                return None
            position = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM chat_messages WHERE conversation_id = ?
                """,
                (current["conversation_id"],),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO chat_messages(
                    id, conversation_id, position, role, content,
                    response_json, response_time_milliseconds, created_at
                ) VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?)
                """,
                (
                    str(result_message_id),
                    current["conversation_id"],
                    position,
                    content,
                    json.dumps(assistant_response, ensure_ascii=False),
                    response_time_milliseconds,
                    completed_timestamp,
                ),
            )
            connection.execute(
                "UPDATE chat_conversations SET updated_at = ? WHERE id = ?",
                (completed_timestamp, current["conversation_id"]),
            )
            row = self._mark_succeeded(
                connection,
                job_id=job_id,
                result_message_id=result_message_id,
                completed_at=completed_timestamp,
            )
        return self._row_to_record(row)

    def _mark_succeeded(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: UUID,
        result_message_id: UUID,
        completed_at: str,
    ) -> sqlite3.Row:
        connection.execute(
            """
            UPDATE jobs
            SET state = ?, step = ?, result_message_id = ?,
                worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                JobState.SUCCEEDED.value,
                JobStep.PERSISTENCE.value,
                str(result_message_id),
                completed_at,
                completed_at,
                str(job_id),
            ),
        )
        self._insert_event(
            connection,
            job_id=job_id,
            state=JobState.SUCCEEDED,
            step=JobStep.PERSISTENCE,
            technical_message="job.succeeded",
            created_at=completed_at,
        )
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise RuntimeError("succeeded job disappeared before commit")
        return row

    def fail_attempt(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        error_code: JobErrorKind,
        safe_message: str,
        diagnostic_code: str | None = None,
        retry_at: datetime | None = None,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Persist a bounded failure and either reschedule or terminate the job."""

        failed_at = now or datetime.now(UTC)
        failed_timestamp = _timestamp(failed_at)
        cleaned_message = " ".join(safe_message.split())[:300]
        if not cleaned_message:
            raise ValueError("safe_message cannot be empty")
        if retry_at is not None:
            _timestamp(retry_at)

        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT attempt, step, type, conversation_id FROM jobs
                WHERE id = ? AND worker_id = ? AND state = ?
                  AND lease_expires_at >= ?
                """,
                (
                    str(job_id),
                    worker_id,
                    JobState.RUNNING.value,
                    failed_timestamp,
                ),
            ).fetchone()
            if current is None:
                return None
            attempt = int(current["attempt"])
            retry_delay = retry_delay_after(attempt)
            should_retry = (
                JOB_ERROR_DISPOSITIONS[error_code] is JobErrorDisposition.RETRYABLE
                and retry_delay is not None
            )
            if should_retry:
                next_attempt_at = failed_at + retry_delay
                if retry_at is not None and retry_at > next_attempt_at:
                    next_attempt_at = retry_at
                target_state = JobState.QUEUED
                available_at = _timestamp(next_attempt_at)
                completed_at = None
                technical_message = "job.retry_scheduled"
            else:
                target_state = JobState.FAILED
                available_at = failed_timestamp
                completed_at = failed_timestamp
                technical_message = "job.failed"

            result_message_id: UUID | None = None
            if target_state is JobState.FAILED and JobType(current["type"]) in {
                JobType.CHAT_ANSWER,
                JobType.DEEP_RESEARCH,
            }:
                result_message_id = self._persist_terminal_notice(
                    connection,
                    job_id=job_id,
                    conversation_id=current["conversation_id"],
                    state=target_state,
                    content=f"**Réponse non produite.** {cleaned_message}",
                    created_at=failed_timestamp,
                    error_code=error_code.value,
                    diagnostic_code=diagnostic_code,
                )

            connection.execute(
                """
                UPDATE jobs
                SET state = ?, available_at = ?,
                    worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    error_code = ?, error_message = ?, result_message_id = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    target_state.value,
                    available_at,
                    error_code.value,
                    cleaned_message,
                    str(result_message_id) if result_message_id is not None else None,
                    failed_timestamp,
                    completed_at,
                    str(job_id),
                ),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                state=target_state,
                step=JobStep(current["step"]),
                technical_message=technical_message,
                created_at=failed_timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def defer_for_quota(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        retry_at: datetime,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Return an owned job to the queue without consuming an attempt."""

        deferred_at = now or datetime.now(UTC)
        deferred_timestamp = _timestamp(deferred_at)
        retry_timestamp = _timestamp(retry_at)
        if retry_at <= deferred_at:
            raise ValueError("quota retry_at must be in the future")
        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT step FROM jobs
                WHERE id = ? AND worker_id = ? AND state = ?
                  AND lease_expires_at >= ?
                """,
                (
                    str(job_id),
                    worker_id,
                    JobState.RUNNING.value,
                    deferred_timestamp,
                ),
            ).fetchone()
            if current is None:
                return None
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, attempt = MAX(attempt - 1, 0), available_at = ?,
                    worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    JobState.QUEUED.value,
                    retry_timestamp,
                    JobErrorKind.QUOTA.value,
                    "Quota ARGO personnel atteint ; reprise automatique planifiée.",
                    deferred_timestamp,
                    str(job_id),
                ),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                state=JobState.QUEUED,
                step=JobStep(current["step"]),
                technical_message="job.quota_deferred",
                created_at=deferred_timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def cancel_queued(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Immediately cancel one queued job before any worker can claim it."""

        cancelled_at = now or datetime.now(UTC)
        cancelled_timestamp = _timestamp(cancelled_at)
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT step, type, conversation_id FROM jobs WHERE id = ? AND state = ?",
                (str(job_id), JobState.QUEUED.value),
            ).fetchone()
            if current is None:
                return None
            result_message_id: UUID | None = None
            if JobType(current["type"]) in {JobType.CHAT_ANSWER, JobType.DEEP_RESEARCH}:
                result_message_id = self._persist_terminal_notice(
                    connection,
                    job_id=job_id,
                    conversation_id=current["conversation_id"],
                    state=JobState.CANCELLED,
                    content=(
                        "**Traitement annulé.** Aucune réponse scientifique n'a été produite "
                        "pour cette question."
                    ),
                    created_at=cancelled_timestamp,
                )
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, result_message_id = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND state = ?
                """,
                (
                    JobState.CANCELLED.value,
                    str(result_message_id) if result_message_id is not None else None,
                    cancelled_timestamp,
                    cancelled_timestamp,
                    str(job_id),
                    JobState.QUEUED.value,
                ),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                state=JobState.CANCELLED,
                step=JobStep(current["step"]),
                technical_message="job.cancelled",
                created_at=cancelled_timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def request_cancellation(
        self,
        job_id: UUID,
        *,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Request cooperative cancellation of a currently running job."""

        requested_at = now or datetime.now(UTC)
        requested_timestamp = _timestamp(requested_at)
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT step FROM jobs WHERE id = ? AND state = ?",
                (str(job_id), JobState.RUNNING.value),
            ).fetchone()
            if current is None:
                return None
            connection.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?",
                (JobState.CANCEL_REQUESTED.value, requested_timestamp, str(job_id)),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                state=JobState.CANCEL_REQUESTED,
                step=JobStep(current["step"]),
                technical_message="job.cancel_requested",
                created_at=requested_timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def acknowledge_cancellation(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Honor a cancellation request at the worker's next safe boundary."""

        cancelled_at = now or datetime.now(UTC)
        cancelled_timestamp = _timestamp(cancelled_at)
        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT step, type, conversation_id FROM jobs
                WHERE id = ? AND state = ? AND worker_id = ?
                  AND lease_expires_at >= ?
                """,
                (
                    str(job_id),
                    JobState.CANCEL_REQUESTED.value,
                    worker_id,
                    cancelled_timestamp,
                ),
            ).fetchone()
            if current is None:
                return None
            result_message_id: UUID | None = None
            if JobType(current["type"]) in {JobType.CHAT_ANSWER, JobType.DEEP_RESEARCH}:
                result_message_id = self._persist_terminal_notice(
                    connection,
                    job_id=job_id,
                    conversation_id=current["conversation_id"],
                    state=JobState.CANCELLED,
                    content=(
                        "**Traitement annulé.** Aucune réponse scientifique n'a été produite "
                        "pour cette question."
                    ),
                    created_at=cancelled_timestamp,
                )
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, worker_id = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    result_message_id = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    JobState.CANCELLED.value,
                    str(result_message_id) if result_message_id is not None else None,
                    cancelled_timestamp,
                    cancelled_timestamp,
                    str(job_id),
                ),
            )
            self._insert_event(
                connection,
                job_id=job_id,
                state=JobState.CANCELLED,
                step=JobStep(current["step"]),
                technical_message="job.cancelled",
                created_at=cancelled_timestamp,
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        return self._row_to_record(row)

    def recover_expired_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> LeaseRecoverySummary:
        """Requeue, fail, or cancel every expired lease in one transaction."""

        recovered_at = now or datetime.now(UTC)
        recovered_timestamp = _timestamp(recovered_at)
        requeued: list[UUID] = []
        failed: list[UUID] = []
        cancelled: list[UUID] = []
        with self.database.transaction() as connection:
            expired = connection.execute(
                """
                SELECT id, state, step, attempt, type, conversation_id
                FROM jobs
                WHERE state IN (?, ?) AND lease_expires_at < ?
                ORDER BY created_at, id
                """,
                (
                    JobState.RUNNING.value,
                    JobState.CANCEL_REQUESTED.value,
                    recovered_timestamp,
                ),
            ).fetchall()
            for row in expired:
                job_id = UUID(row["id"])
                current_state = JobState(row["state"])
                retry_delay = retry_delay_after(int(row["attempt"]))
                if current_state is JobState.CANCEL_REQUESTED:
                    target_state = JobState.CANCELLED
                    available_at = recovered_timestamp
                    completed_at = recovered_timestamp
                    error_code = None
                    error_message = None
                    technical_message = "job.cancelled_after_lease"
                    cancelled.append(job_id)
                elif retry_delay is not None:
                    target_state = JobState.QUEUED
                    available_at = _timestamp(recovered_at + retry_delay)
                    completed_at = None
                    error_code = JobErrorKind.TIMEOUT.value
                    error_message = "Le worker local a été interrompu ; reprise planifiée."
                    technical_message = "job.lease_recovered"
                    requeued.append(job_id)
                else:
                    target_state = JobState.FAILED
                    available_at = recovered_timestamp
                    completed_at = recovered_timestamp
                    error_code = JobErrorKind.TIMEOUT.value
                    error_message = "Le nombre maximal de reprises locales est atteint."
                    technical_message = "job.failed"
                    failed.append(job_id)

                result_message_id: UUID | None = None
                if target_state in {JobState.FAILED, JobState.CANCELLED} and JobType(
                    row["type"]
                ) in {JobType.CHAT_ANSWER, JobType.DEEP_RESEARCH}:
                    notice_content = (
                        "**Traitement annulé.** Aucune réponse scientifique n'a été produite "
                        "pour cette question."
                        if target_state is JobState.CANCELLED
                        else f"**Réponse non produite.** {error_message}"
                    )
                    result_message_id = self._persist_terminal_notice(
                        connection,
                        job_id=job_id,
                        conversation_id=row["conversation_id"],
                        state=target_state,
                        content=notice_content,
                        created_at=recovered_timestamp,
                        error_code=error_code,
                        diagnostic_code=(
                            "worker_lease_exhausted" if target_state is JobState.FAILED else None
                        ),
                    )

                connection.execute(
                    """
                    UPDATE jobs
                    SET state = ?, available_at = ?,
                        worker_id = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                        error_code = ?, error_message = ?, result_message_id = ?,
                        updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        target_state.value,
                        available_at,
                        error_code,
                        error_message,
                        str(result_message_id) if result_message_id is not None else None,
                        recovered_timestamp,
                        completed_at,
                        str(job_id),
                    ),
                )
                self._insert_event(
                    connection,
                    job_id=job_id,
                    state=target_state,
                    step=JobStep(row["step"]),
                    technical_message=technical_message,
                    created_at=recovered_timestamp,
                )

        return LeaseRecoverySummary(
            requeued=tuple(requeued),
            failed=tuple(failed),
            cancelled=tuple(cancelled),
        )

    def _insert_initial_event(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: UUID,
        created_at: str,
    ) -> None:
        self._insert_event(
            connection,
            job_id=job_id,
            state=JobState.QUEUED,
            step=JobStep.WAITING,
            technical_message="job.enqueued",
            created_at=created_at,
        )

    @staticmethod
    def _assert_active_limit(
        connection: sqlite3.Connection,
        conversation_id: UUID,
    ) -> None:
        active_count = connection.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE conversation_id = ?
              AND state IN ('queued', 'running', 'cancel_requested')
            """,
            (str(conversation_id),),
        ).fetchone()[0]
        if active_count >= MAX_ACTIVE_JOBS_PER_CONVERSATION:
            raise ActiveJobLimitError()

    def _insert_queued_job(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: UUID,
        payload: JobPayload,
        job_type: JobType = JobType.CHAT_ANSWER,
        user_message_id: UUID,
        priority: int,
        available_at: str,
        created_at: str,
    ) -> sqlite3.Row:
        active_evaluation = connection.execute(
            """
            SELECT id FROM jobs
            WHERE state IN ('queued', 'running', 'cancel_requested')
              AND json_extract(payload_json, '$.evaluation_run_id') IS NOT NULL
            LIMIT 1
            """
        ).fetchone()
        if active_evaluation is not None:
            raise EvaluationRunBusyError(
                "no new durable job can be queued while an evaluation cell is active"
            )
        connection.execute(
            """
            INSERT INTO jobs(
                id, type, state, step, payload_json, priority, attempt, available_at,
                conversation_id, user_message_id, client_request_id,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 0, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                str(job_id),
                job_type.value,
                JobState.QUEUED.value,
                JobStep.WAITING.value,
                payload.model_dump_json(),
                priority,
                available_at,
                str(payload.conversation_id),
                str(user_message_id),
                str(payload.client_request_id),
                created_at,
                created_at,
            ),
        )
        self._insert_initial_event(connection, job_id=job_id, created_at=created_at)
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise RuntimeError("queued job disappeared inside its transaction")
        return row

    @staticmethod
    def _job_type_for_payload(payload: JobPayload) -> JobType:
        if isinstance(payload, ChatAnswerPayload):
            return JobType.CHAT_ANSWER
        if isinstance(payload, DeepResearchPayload):
            return JobType.DEEP_RESEARCH
        if isinstance(payload, WeeklyMaintenancePayload):
            return JobType.WEEKLY_MAINTENANCE
        if isinstance(payload, LongSynthesisPayload):
            return JobType.LONG_SYNTHESIS
        if isinstance(payload, CorpusIngestionPayload):
            return JobType.CORPUS_INGESTION
        raise TypeError("unsupported durable job payload")

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        job_id: UUID,
        state: JobState,
        step: JobStep,
        technical_message: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_events(job_id, state, step, technical_message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(job_id),
                state.value,
                step.value,
                technical_message,
                created_at,
            ),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> JobRecord:
        job_type = JobType(row["type"])
        if job_type is JobType.CHAT_ANSWER:
            payload = ChatAnswerPayload.model_validate_json(row["payload_json"])
        elif job_type is JobType.DEEP_RESEARCH:
            payload = DeepResearchPayload.model_validate_json(row["payload_json"])
        elif job_type is JobType.WEEKLY_MAINTENANCE:
            payload = WeeklyMaintenancePayload.model_validate_json(row["payload_json"])
        elif job_type is JobType.LONG_SYNTHESIS:
            payload = LongSynthesisPayload.model_validate_json(row["payload_json"])
        else:
            payload = CorpusIngestionPayload.model_validate_json(row["payload_json"])
        return JobRecord(
            id=UUID(row["id"]),
            type=job_type,
            state=JobState(row["state"]),
            step=JobStep(row["step"]),
            payload=payload,
            priority=row["priority"],
            attempt=row["attempt"],
            available_at=datetime.fromisoformat(row["available_at"]),
            worker_id=row["worker_id"],
            lease_expires_at=_parse_timestamp(row["lease_expires_at"]),
            heartbeat_at=_parse_timestamp(row["heartbeat_at"]),
            conversation_id=UUID(row["conversation_id"]),
            user_message_id=UUID(row["user_message_id"]),
            result_message_id=(
                UUID(row["result_message_id"]) if row["result_message_id"] is not None else None
            ),
            client_request_id=UUID(row["client_request_id"]),
            error_code=(JobErrorKind(row["error_code"]) if row["error_code"] else None),
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            started_at=_parse_timestamp(row["started_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
        )
