"""Auditable prompt-profile evaluation runs over the durable chat queue."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.jobs.contracts import ChatAnswerPayload, JobState


class EvaluationJobAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    conversation_id: str
    question_id: str
    profile: str
    state: JobState
    attempt: int = Field(ge=0)
    user_message_count: int = Field(ge=0)
    assistant_message_count: int = Field(ge=0)
    result_kind: str | None = None
    problems: list[str] = Field(default_factory=list)


class EvaluationRunAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    jobs: list[EvaluationJobAudit]
    unique_cells: int = Field(ge=0)
    succeeded_cells: int = Field(ge=0)
    active_jobs: int = Field(ge=0)
    maximum_concurrent_executions: int = Field(ge=0)
    outcome_counts: dict[str, int]
    problem_counts: dict[str, int]
    complete: bool
    reliable: bool


def _maximum_concurrency(intervals: list[tuple[datetime, datetime]]) -> int:
    boundaries: list[tuple[datetime, int]] = []
    for started_at, completed_at in intervals:
        boundaries.append((started_at, 1))
        boundaries.append((completed_at, -1))
    active = maximum = 0
    for _timestamp, delta in sorted(boundaries, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def audit_evaluation_run(database_path: str | Path, run_id: str) -> EvaluationRunAudit:
    """Check question isolation, terminal visibility and response identity for one run."""

    path = Path(database_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    jobs: list[EvaluationJobAudit] = []
    cell_success: set[tuple[str, str]] = set()
    cells: set[tuple[str, str]] = set()
    problem_counter: Counter[str] = Counter()
    outcome_counter: Counter[str] = Counter()
    intervals: list[tuple[datetime, datetime]] = []
    active_jobs = 0
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM jobs
            WHERE type = 'chat_answer'
              AND json_extract(payload_json, '$.evaluation_run_id') = ?
            ORDER BY created_at, id
            """,
            (run_id,),
        ).fetchall()
        for row in rows:
            payload = ChatAnswerPayload.model_validate_json(row["payload_json"])
            messages = connection.execute(
                """
                SELECT id, role, content, response_json
                FROM chat_messages
                WHERE conversation_id = ?
                ORDER BY position
                """,
                (row["conversation_id"],),
            ).fetchall()
            user_messages = [message for message in messages if message["role"] == "user"]
            assistant_messages = [message for message in messages if message["role"] == "assistant"]
            problems: list[str] = []
            expected_hash = sha256(payload.message.encode("utf-8")).hexdigest()
            if payload.evaluation_question_sha256 != expected_hash:
                problems.append("question_fingerprint_mismatch")
            if len(user_messages) != 1 or user_messages[0]["content"] != payload.message:
                problems.append("conversation_question_contamination")
            result_message = next(
                (
                    message
                    for message in assistant_messages
                    if message["id"] == row["result_message_id"]
                ),
                None,
            )
            response = None
            if result_message is not None and result_message["response_json"] is not None:
                response = json.loads(result_message["response_json"])
            state = JobState(row["state"])
            result_kind = response.get("kind") if isinstance(response, dict) else None
            if state is JobState.SUCCEEDED:
                outcome_counter[
                    (
                        str(response.get("generation_status", "generated"))
                        if isinstance(response, dict)
                        else "missing_success_response"
                    )
                ] += 1
                if result_message is None or not isinstance(response, dict):
                    problems.append("successful_job_without_visible_response")
                else:
                    trace = response.get("evaluation")
                    if not isinstance(trace, dict):
                        problems.append("successful_response_without_evaluation_trace")
                    elif trace != {
                        "run_id": payload.evaluation_run_id,
                        "question_id": payload.evaluation_question_id,
                        "profile": payload.evaluation_profile,
                        "question_sha256": expected_hash,
                    }:
                        problems.append("successful_response_trace_mismatch")
                    if response.get("message") != payload.message:
                        problems.append("successful_response_question_mismatch")
                cell_success.add(
                    (str(payload.evaluation_profile), str(payload.evaluation_question_id))
                )
            elif state in {JobState.FAILED, JobState.CANCELLED}:
                outcome_counter[state.value] += 1
                if result_message is None or result_kind != "job_terminal_notice":
                    problems.append("terminal_job_without_visible_notice")
                elif response.get("job_id") != row["id"] or response.get("state") != state.value:
                    problems.append("terminal_notice_identity_mismatch")
            else:
                outcome_counter[state.value] += 1
                active_jobs += 1
            cells.add((str(payload.evaluation_profile), str(payload.evaluation_question_id)))
            if row["started_at"] is not None and row["completed_at"] is not None:
                intervals.append(
                    (
                        datetime.fromisoformat(row["started_at"]),
                        datetime.fromisoformat(row["completed_at"]),
                    )
                )
            problem_counter.update(problems)
            jobs.append(
                EvaluationJobAudit(
                    job_id=row["id"],
                    conversation_id=row["conversation_id"],
                    question_id=str(payload.evaluation_question_id),
                    profile=str(payload.evaluation_profile),
                    state=state,
                    attempt=int(row["attempt"]),
                    user_message_count=len(user_messages),
                    assistant_message_count=len(assistant_messages),
                    result_kind=result_kind,
                    problems=problems,
                )
            )
    maximum_concurrency = _maximum_concurrency(intervals)
    if maximum_concurrency > 1:
        problem_counter["concurrent_evaluation_executions"] += 1
    complete = bool(jobs) and active_jobs == 0
    return EvaluationRunAudit(
        run_id=run_id,
        jobs=jobs,
        unique_cells=len(cells),
        succeeded_cells=len(cell_success),
        active_jobs=active_jobs,
        maximum_concurrent_executions=maximum_concurrency,
        outcome_counts=dict(sorted(outcome_counter.items())),
        problem_counts=dict(sorted(problem_counter.items())),
        complete=complete,
        reliable=complete and not problem_counter,
    )
