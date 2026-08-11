from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.schemas import (
    ChatJobSubmitRequest,
    ChatJobSubmitResponse,
    PersistedUserMessage,
)
from app.database.sqlite import Database
from app.jobs.contracts import ChatAnswerPayload, JobPublic, JobState, JobStep, JobType
from app.jobs.repository import JobRepository
from app.main import create_app


def test_chat_job_submission_is_strict_and_requires_client_uuid() -> None:
    request_id = uuid4()
    request = ChatJobSubmitRequest(
        message="  Question   scientifique ? ",
        client_request_id=request_id,
    )

    assert request.message == "Question scientifique ?"
    assert request.client_request_id == request_id
    assert request.use_external_sources is False
    assert request.mode == "quick"
    assert request.interaction_mode == "auto"
    assert request.answer_effort.value == "balanced"

    legacy_request = ChatJobSubmitRequest(
        message="Question scientifique ?",
        client_request_id=uuid4(),
        answer_intensity="deep",
    )
    assert legacy_request.answer_effort.value == "deep"
    assert "answer_intensity" not in legacy_request.model_dump(mode="json")

    with pytest.raises(ValidationError):
        ChatJobSubmitRequest(message="Question", client_request_id="not-a-uuid")
    with pytest.raises(ValidationError):
        ChatJobSubmitRequest(
            message="Question",
            client_request_id=request_id,
            unknown="forbidden",
        )
    with pytest.raises(ValidationError):
        ChatJobSubmitRequest(
            message="Question",
            client_request_id=request_id,
            interaction_mode="unsupported",
        )
    with pytest.raises(ValidationError):
        ChatJobSubmitRequest(
            message="Question",
            client_request_id=request_id,
            answer_effort="balanced",
            answer_intensity="deep",
        )


def test_chat_job_accepted_response_has_no_argo_result() -> None:
    now = datetime.now(UTC)
    response = ChatJobSubmitResponse(
        job=JobPublic(
            id=uuid4(),
            conversation_id=uuid4(),
            type=JobType.CHAT_ANSWER,
            state=JobState.QUEUED,
            step=JobStep.WAITING,
            attempt=0,
            available_at=now,
            created_at=now,
            updated_at=now,
        ),
        user_message=PersistedUserMessage(
            id=uuid4(),
            content="Question scientifique ?",
            created_at=now,
        ),
    )

    serialized = response.model_dump(mode="json")
    assert set(serialized) == {"job", "user_message"}
    assert serialized["job"]["state"] == "queued"
    assert "answer" not in serialized
    assert "result" not in serialized


def test_get_job_route_returns_safe_projection_and_404(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation_id = uuid4()
    message_id = uuid4()
    with database.transaction() as connection:
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
    job = JobRepository(database.path).enqueue(
        ChatAnswerPayload(
            message="Question",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/jobs/{job.id}")
        missing = client.get(f"/api/jobs/{uuid4()}")
        conversation = client.get(f"/api/chatbot/conversations/{conversation_id}")
        conversations = client.get("/api/chatbot/conversations")

    assert response.status_code == 200
    assert response.json()["id"] == str(job.id)
    assert response.json()["state"] == "queued"
    assert "payload" not in response.json()
    assert "worker_id" not in response.json()
    assert missing.status_code == 404
    assert conversation.status_code == 200
    assert [active["id"] for active in conversation.json()["active_jobs"]] == [str(job.id)]
    summary = next(
        item for item in conversations.json()["conversations"] if item["id"] == str(conversation_id)
    )
    assert summary["active_job_count"] == 1


def test_cancel_and_retry_reject_invalid_transitions_with_409(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation_id = uuid4()
    message_id = uuid4()
    with database.transaction() as connection:
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
    repository = JobRepository(database.path)
    cancellable = repository.enqueue(
        ChatAnswerPayload(
            message="Question",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )
    failed = repository.enqueue(
        ChatAnswerPayload(
            message="Question échouée",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
    )
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (str(failed.id),))

    retry_request_id = uuid4()
    with TestClient(create_app(settings)) as client:
        cancelled = client.post(f"/api/jobs/{cancellable.id}/cancel")
        invalid_cancel = client.post(f"/api/jobs/{cancellable.id}/cancel")
        retried = client.post(
            f"/api/jobs/{failed.id}/retry",
            json={"client_request_id": str(retry_request_id)},
        )
        original_after_retry = client.get(f"/api/jobs/{failed.id}")
        retried_after_creation = client.get(f"/api/jobs/{retried.json()['id']}")
        invalid_retry = client.post(
            f"/api/jobs/{cancellable.id}/retry",
            json={"client_request_id": str(uuid4())},
        )

    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert invalid_cancel.status_code == 409
    assert retried.status_code == 202
    assert retried.json()["state"] == "queued"
    assert retried.json()["id"] != str(failed.id)
    assert original_after_retry.json()["state"] == "failed"
    assert retried_after_creation.json()["state"] == "queued"
    assert original_after_retry.json()["id"] != retried_after_creation.json()["id"]
    assert invalid_retry.status_code == 409


def test_chat_submission_atomically_returns_accepted_job_and_user_message(settings) -> None:
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/chatbot/conversations",
            json={"title": "Travail durable"},
        )
        conversation_id = created.json()["id"]
        accepted = client.post(
            f"/api/chatbot/conversations/{conversation_id}/jobs",
            json={
                "message": "Question asynchrone",
                "client_request_id": str(uuid4()),
                "use_external_sources": False,
                "analyze_figures": True,
            },
        )

    assert accepted.status_code == 202
    body = accepted.json()
    assert body["job"]["state"] == "queued"
    assert body["job"]["conversation_id"] == conversation_id
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["content"] == "Question asynchrone"
    assert "answer" not in body

    database = Database(settings.paths.database_path)
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1
        stored_payload = json.loads(
            connection.execute("SELECT payload_json FROM jobs").fetchone()["payload_json"]
        )
    assert stored_payload["analyze_figures"] is True


def test_deep_research_submission_fails_closed_without_promotion(settings) -> None:
    with TestClient(create_app(settings)) as client:
        conversation_id = client.post(
            "/api/chatbot/conversations",
            json={"title": "Gate fermé"},
        ).json()["id"]
        rejected = client.post(
            f"/api/chatbot/conversations/{conversation_id}/jobs",
            json={
                "message": "Analyse approfondie",
                "client_request_id": str(uuid4()),
                "mode": "deep_research",
            },
        )

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "deep_research_unavailable"
    database = Database(settings.paths.database_path)
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_network_retry_returns_same_job_and_message(settings) -> None:
    request_id = uuid4()
    with TestClient(create_app(settings)) as client:
        conversation_id = client.post("/api/chatbot/conversations", json={"title": "Retry"}).json()[
            "id"
        ]
        payload = {
            "message": "Question rejouée",
            "client_request_id": str(request_id),
        }
        first = client.post(f"/api/chatbot/conversations/{conversation_id}/jobs", json=payload)
        retried = client.post(f"/api/chatbot/conversations/{conversation_id}/jobs", json=payload)

    assert first.status_code == 202
    assert retried.status_code == 202
    assert retried.json()["job"]["id"] == first.json()["job"]["id"]
    assert retried.json()["user_message"]["id"] == first.json()["user_message"]["id"]

    database = Database(settings.paths.database_path)
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1


def test_job_routes_cover_running_cancel_idempotent_retry_and_missing(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    repository = JobRepository(database.path)
    conversation_id = uuid4()
    message_id = uuid4()
    with database.transaction() as connection:
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
    now = datetime.now(UTC)
    repository.enqueue(
        ChatAnswerPayload(
            message="Question active",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    running = repository.claim_next(
        worker_id="worker-test", lease_duration=timedelta(minutes=5), now=now
    )
    assert running is not None
    failed = repository.enqueue(
        ChatAnswerPayload(
            message="Question échouée",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        user_message_id=message_id,
        now=now,
    )
    with database.transaction() as connection:
        connection.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (str(failed.id),))
    retry_id = uuid4()
    missing_id = uuid4()

    with TestClient(create_app(settings)) as client:
        cancellation = client.post(f"/api/jobs/{running.id}/cancel")
        first_retry = client.post(
            f"/api/jobs/{failed.id}/retry",
            json={"client_request_id": str(retry_id)},
        )
        second_retry = client.post(
            f"/api/jobs/{failed.id}/retry",
            json={"client_request_id": str(retry_id)},
        )
        missing_get = client.get(f"/api/jobs/{missing_id}")
        missing_cancel = client.post(f"/api/jobs/{missing_id}/cancel")
        missing_retry = client.post(
            f"/api/jobs/{missing_id}/retry",
            json={"client_request_id": str(uuid4())},
        )

    assert cancellation.status_code == 200
    assert cancellation.json()["state"] == "cancel_requested"
    assert first_retry.status_code == 202
    assert second_retry.json()["id"] == first_retry.json()["id"]
    assert {missing_get.status_code, missing_cancel.status_code, missing_retry.status_code} == {404}


def test_active_job_limit_is_atomic_and_actionable(settings) -> None:
    with TestClient(create_app(settings)) as client:
        conversation_id = client.post(
            "/api/chatbot/conversations", json={"title": "Limite"}
        ).json()["id"]
        responses = [
            client.post(
                f"/api/chatbot/conversations/{conversation_id}/jobs",
                json={
                    "message": f"Question {number}",
                    "client_request_id": str(uuid4()),
                },
            )
            for number in range(1, 5)
        ]

    assert [response.status_code for response in responses] == [202, 202, 202, 409]
    assert responses[-1].json()["detail"] == {
        "code": "active_job_limit",
        "message": "Attendez la fin d'un travail actif avant d'en envoyer un autre.",
        "limit": 3,
    }
    database = Database(settings.paths.database_path)
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 3
