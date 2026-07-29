from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_pilot_feedback_persists_only_explicit_minimal_content(settings) -> None:
    with TestClient(create_app(settings)) as client:
        rejected = client.post(
            "/api/pilot-feedback",
            json={
                "type": "functional",
                "step": "Réouverture",
                "description": "La carte reste chargée.",
                "conversation_id": "must-not-be-accepted",
            },
        )
        created = client.post(
            "/api/pilot-feedback",
            json={
                "type": "functional",
                "step": "  Réouverture  ",
                "description": "  La carte reste chargée.  ",
            },
        )
        listed = client.get("/api/pilot-feedback")

    assert rejected.status_code == 422
    assert created.status_code == 201
    assert created.json()["type"] == "functional"
    assert created.json()["step"] == "Réouverture"
    assert created.json()["description"] == "La carte reste chargée."
    assert listed.status_code == 200
    assert listed.json() == [created.json()]

    database = create_app(settings).state.database
    with database.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(pilot_defects)")}
    assert columns == {"id", "defect_type", "step", "description", "created_at"}
    assert not columns & {"question", "answer", "chat", "conversation_id", "job_id", "document_id"}


def test_pilot_feedback_rejects_empty_or_unknown_fields(settings) -> None:
    with TestClient(create_app(settings)) as client:
        empty = client.post(
            "/api/pilot-feedback",
            json={"type": "other", "step": " ", "description": "Description"},
        )
        unknown_type = client.post(
            "/api/pilot-feedback",
            json={"type": "chat", "step": "Étape", "description": "Description"},
        )

    assert empty.status_code == 422
    assert unknown_type.status_code == 422
