from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.evaluation.campaign import (
    CampaignExecutionError,
    EvaluationCampaignRunner,
    EvaluationCampaignSpec,
)
from app.jobs.contracts import ChatAnswerPayload, JobType
from app.jobs.repository import JobRepository
from app.jobs.worker import DurableJobWorker, JobHandlerRegistry, JobHandlerResult
from app.models.chatbot import ChatbotEvaluationTrace, ChatbotResult


class SuccessfulEvaluationHandler:
    def handle(self, job, context) -> JobHandlerResult:
        del context
        payload = job.payload
        result = ChatbotResult(
            message=payload.message,
            retrieval_query=payload.message,
            answer_markdown="Réponse de test visible.",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
            evaluation=ChatbotEvaluationTrace(
                run_id=payload.evaluation_run_id,
                question_id=payload.evaluation_question_id,
                profile=payload.evaluation_profile,
                question_sha256=payload.evaluation_question_sha256,
            ),
        )
        return JobHandlerResult(
            assistant_content=result.answer_markdown,
            assistant_response=result.model_dump(mode="json"),
            response_time_milliseconds=100,
        )


def _spec(run_id: str = "campaign-test") -> EvaluationCampaignSpec:
    return EvaluationCampaignSpec.model_validate(
        {
            "run_id": run_id,
            "cells": [
                {"question_id": "Q1", "profile": "p0", "message": "Question une"},
                {"question_id": "Q2", "profile": "p1", "message": "Question deux"},
            ],
        }
    )


def test_campaign_runs_cells_sequentially_and_persists_a_reliable_audit(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    worker = DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry({JobType.CHAT_ANSWER: SuccessfulEvaluationHandler()}),
        worker_id="campaign-worker",
    )
    runner = EvaluationCampaignRunner(
        repository,
        tmp_path / "campaign",
        poll_seconds=0,
        sleeper=lambda _seconds: None,
        on_poll=worker.run_once,
    )

    result = runner.run(_spec())

    assert result.complete is True
    assert result.reliable is True
    assert result.terminal_cells == 2
    assert result.succeeded_cells == 2
    assert result.audit.maximum_concurrent_executions == 1
    assert result.audit.outcome_counts == {"generated": 2}
    state = json.loads((tmp_path / "campaign" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert len(state["cells"]) == 2
    assert (tmp_path / "campaign" / "events.jsonl").is_file()
    assert (tmp_path / "campaign" / "audit.json").is_file()
    assert "Fiable : `true`" in (tmp_path / "campaign" / "report.md").read_text(encoding="utf-8")

    resumed = runner.run(_spec())

    assert resumed.reliable is True
    with repository.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_campaign_cancels_a_queued_cell_instead_of_leaving_it_without_output(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    runner = EvaluationCampaignRunner(
        repository,
        tmp_path / "campaign",
        poll_seconds=0,
        job_timeout_seconds=0.001,
        cancellation_grace_seconds=0.1,
        sleeper=lambda _seconds: None,
    )
    spec = _spec("campaign-timeout")

    result = runner.run(spec)

    assert result.complete is False
    assert result.reliable is False
    assert result.terminal_cells == 1
    assert result.audit.outcome_counts == {"cancelled": 1}
    state = json.loads((tmp_path / "campaign" / "state.json").read_text(encoding="utf-8"))
    first = state["cells"]["p0:Q1"]
    assert first["state"] == "cancelled"
    assert first["generation_status"] == "terminal_notice"
    conversation = repository.database.chat_conversation(first["conversation_id"])
    assert conversation is not None
    assert [message["role"] for message in conversation["messages"]] == ["user", "assistant"]
    with pytest.raises(CampaignExecutionError, match="requires a new run_id"):
        runner.run(spec)


def test_campaign_waits_until_quota_retry_at_before_applying_its_timeout(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()

    class Clock:
        now = datetime.now(UTC) + timedelta(seconds=1)
        monotonic_value = 0.0

        @classmethod
        def sleep(cls, _seconds: float) -> None:
            cls.now += timedelta(seconds=1)
            cls.monotonic_value += 1.0

    phase: dict[str, object] = {"value": "initial"}

    def drive_job() -> None:
        if phase["value"] == "initial":
            claimed = repository.claim_next(
                worker_id="quota-worker",
                lease_duration=timedelta(minutes=5),
                now=Clock.now,
            )
            assert claimed is not None
            retry_at = Clock.now + timedelta(seconds=3)
            repository.defer_for_quota(
                claimed.id,
                worker_id="quota-worker",
                retry_at=retry_at,
                now=Clock.now,
            )
            phase.update({"value": "deferred", "retry_at": retry_at})
            return
        if phase["value"] != "deferred" or Clock.now < phase["retry_at"]:
            return
        claimed = repository.claim_next(
            worker_id="quota-worker",
            lease_duration=timedelta(minutes=5),
            now=Clock.now,
        )
        assert claimed is not None
        payload = claimed.payload
        result = ChatbotResult(
            message=payload.message,
            retrieval_query=payload.message,
            answer_markdown="Réponse après quota.",
            sources=[],
            warnings=[],
            model="test-model",
            local_result_count=0,
            external_result_count=0,
            external_enrichment_used=False,
            prompt_tokens=1,
            completion_tokens=1,
            duration_seconds=0.1,
            evaluation=ChatbotEvaluationTrace(
                run_id=payload.evaluation_run_id,
                question_id=payload.evaluation_question_id,
                profile=payload.evaluation_profile,
                question_sha256=payload.evaluation_question_sha256,
            ),
        )
        repository.persist_result_and_succeed(
            claimed.id,
            worker_id="quota-worker",
            assistant_content=result.answer_markdown,
            assistant_response=result.model_dump(mode="json"),
            response_time_milliseconds=100,
            now=Clock.now,
        )
        phase["value"] = "succeeded"

    spec = EvaluationCampaignSpec.model_validate(
        {
            "run_id": "campaign-quota",
            "cells": [{"question_id": "Q1", "profile": "p0", "message": "Question quota"}],
        }
    )
    runner = EvaluationCampaignRunner(
        repository,
        tmp_path / "campaign",
        poll_seconds=0,
        job_timeout_seconds=0.5,
        cancellation_grace_seconds=1,
        sleeper=Clock.sleep,
        monotonic_clock=lambda: Clock.monotonic_value,
        utc_now=lambda: Clock.now,
        on_poll=drive_job,
    )

    result = runner.run(spec)

    assert phase["value"] == "succeeded"
    assert Clock.monotonic_value >= 3
    assert result.complete is True
    assert result.reliable is True


def test_campaign_refuses_to_submit_when_an_unrelated_job_is_active(tmp_path) -> None:
    repository = JobRepository(tmp_path / "queue.sqlite3")
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Travail existant")
    repository.enqueue_chat(
        ChatAnswerPayload(
            message="Question déjà active",
            conversation_id=conversation["id"],
            client_request_id="11111111-1111-4111-8111-111111111111",
        )
    )
    runner = EvaluationCampaignRunner(repository, tmp_path / "campaign")

    with pytest.raises(CampaignExecutionError, match="queue is not idle"):
        runner.run(_spec("campaign-busy"))

    state = json.loads((tmp_path / "campaign" / "state.json").read_text(encoding="utf-8"))
    assert state["cells"] == {}
    assert state["stop_diagnostic"] == (
        "the durable queue is not idle; no evaluation question was submitted"
    )
