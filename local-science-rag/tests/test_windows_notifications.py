from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from app.config import Settings
from app.desktop.notifications import WindowsJobNotifier
from app.jobs.contracts import ChatAnswerPayload, JobState, JobStep, JobType
from app.jobs.repository import JobRecord


def _job() -> JobRecord:
    now = datetime.now(UTC)
    conversation_id = uuid4()
    return JobRecord(
        id=uuid4(),
        type=JobType.CHAT_ANSWER,
        state=JobState.SUCCEEDED,
        step=JobStep.PERSISTENCE,
        payload=ChatAnswerPayload(
            message="CONTENU_SENSIBLE",
            conversation_id=conversation_id,
            client_request_id=uuid4(),
        ),
        priority=100,
        attempt=1,
        available_at=now,
        worker_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        conversation_id=conversation_id,
        user_message_id=UUID("11111111-1111-4111-8111-111111111111"),
        client_request_id=UUID("22222222-2222-4222-8222-222222222222"),
        result_message_id=UUID("33333333-3333-4333-8333-333333333333"),
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=now + timedelta(seconds=1),
    )


def test_notification_is_opt_in_and_never_contains_job_content(monkeypatch, settings) -> None:
    job = _job()
    disabled = WindowsJobNotifier(settings)
    assert disabled.notify(job) is False

    payload = settings.model_dump(mode="python")
    payload["notifications"]["enabled"] = True
    enabled = WindowsJobNotifier(Settings.model_validate(payload))
    popen = MagicMock()
    monkeypatch.setattr("app.desktop.notifications.sys.platform", "win32")
    monkeypatch.setattr("app.desktop.notifications.subprocess.Popen", popen)

    assert enabled.notify(job) is True
    command = popen.call_args.args[0]
    serialized = " ".join(str(item) for item in command)
    assert "CONTENU_SENSIBLE" not in serialized
    assert str(job.id) not in serialized
    assert "Réponse scientifique terminée." in serialized
