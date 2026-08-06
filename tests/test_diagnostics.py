from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.diagnostics import (
    _worker_rss_bytes,
    build_readiness_report,
    build_runtime_diagnostics,
    worker_heartbeat_path,
)
from app.jobs.contracts import ChatAnswerPayload
from app.jobs.repository import JobRepository
from app.llm.argo_client import ArgoHealth
from app.main import create_app


def _ready_corpus(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE articles(id TEXT);
            CREATE TABLE chunks(id INTEGER);
            INSERT INTO articles VALUES ('article');
            INSERT INTO chunks VALUES (1);
            """
        )


def test_readiness_probes_models_worker_corpus_disk_and_content_free_queue(
    settings, monkeypatch
) -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Diagnostic")
    repository.enqueue_chat(
        ChatAnswerPayload(
            message="sentinel question must never enter diagnostics",
            conversation_id=UUID(conversation["id"]),
            client_request_id=uuid4(),
        ),
        now=now - timedelta(seconds=65),
    )
    _ready_corpus(settings.paths.common_database_path)
    heartbeat = worker_heartbeat_path(settings)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(
        json.dumps({"schema_version": 1, "pid": 123, "updated_at": now.isoformat()}),
        encoding="utf-8",
    )
    monkeypatch.setenv(settings.argo.api_key_env, "sentinel-key")
    calls = {"models": 0}

    def model_probe() -> ArgoHealth:
        calls["models"] += 1
        return ArgoHealth(
            reachable=True,
            base_url="https://argo.invalid/api",
            configured_model=settings.argo.model,
            model_available=True,
            available_models=[settings.argo.model],
            api_key_configured=True,
            error=None,
        )

    report = build_readiness_report(
        settings,
        argo_probe=model_probe,
        now=now,
        disk_free_bytes=3 * 1024**3,
    )

    assert report["ready"] is True
    assert calls == {"models": 1}
    assert report["queue"] == {
        "depth": 1,
        "queued": 1,
        "running": 0,
        "cancel_requested": 0,
        "oldest_created_at": (now - timedelta(seconds=65)).isoformat(),
        "oldest_age_seconds": 65,
    }
    serialized = json.dumps(report)
    assert "sentinel question" not in serialized
    assert "client_request_id" not in serialized


def test_diagnostic_api_returns_actionable_checks(settings, monkeypatch) -> None:
    payload = {
        "schema_version": 1,
        "ready": False,
        "checked_at": "2026-07-22T12:00:00+00:00",
        "checks": {
            "argo": {
                "state": "blocked",
                "message": "Clé ARGO absente.",
                "action": "Ajouter la clé.",
            }
        },
        "queue": {"depth": 0, "oldest_age_seconds": None},
    }
    monkeypatch.setattr(
        "app.api.diagnostics.build_readiness_report",
        lambda _settings: payload,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/diagnostics/readiness")

    assert response.status_code == 200
    assert response.json() == payload


def test_runtime_diagnostics_exposes_only_safe_active_job_metadata(settings) -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Diagnostic")
    job = repository.enqueue_chat(
        ChatAnswerPayload(
            message="sentinel question must never enter runtime diagnostics",
            conversation_id=UUID(conversation["id"]),
            client_request_id=uuid4(),
        ),
        now=now - timedelta(seconds=20),
    ).job
    heartbeat = worker_heartbeat_path(settings)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(
        json.dumps({"schema_version": 1, "pid": 123, "updated_at": now.isoformat()}),
        encoding="utf-8",
    )

    report = build_runtime_diagnostics(settings, now=now)

    assert report["worker"] == {
        "state": "healthy",
        "heartbeat_at": now.isoformat(),
        "heartbeat_age_seconds": 0,
    }
    assert report["active_jobs"] == [
        {
            "id": job.id,
            "type": job.type,
            "state": job.state,
            "step": job.step,
            "created_at": job.created_at,
            "heartbeat_at": None,
        }
    ]
    serialized = json.dumps(report, default=str)
    assert "sentinel question" not in serialized
    assert "conversation_id" not in serialized
    assert "pid" not in serialized


def test_worker_rss_lookup_is_best_effort_for_missing_or_inaccessible_process(monkeypatch) -> None:
    class ProcessError(Exception):
        pass

    def unavailable_process(_pid: int):
        raise ProcessError("worker has exited")

    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Error=ProcessError, Process=unavailable_process),
    )

    assert _worker_rss_bytes(None) is None
    assert _worker_rss_bytes(123) is None


def test_runtime_diagnostic_api_reports_stale_worker_for_active_work(settings) -> None:
    now = datetime.now(UTC)
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    conversation = repository.database.create_chat_conversation("Diagnostic")
    repository.enqueue_chat(
        ChatAnswerPayload(
            message="diagnostic job",
            conversation_id=UUID(conversation["id"]),
            client_request_id=uuid4(),
        )
    )
    heartbeat = worker_heartbeat_path(settings)
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": 123,
                "updated_at": (now - timedelta(seconds=6)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/system/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker"]["state"] == "stale"
    assert {warning["code"] for warning in payload["warnings"]} == {
        "worker_heartbeat_stale",
        "active_work_without_healthy_worker",
    }
