from uuid import uuid4

from fastapi.testclient import TestClient

from app.corpora import LOCAL_PROFILE_ENV
from app.database.sqlite import Database
from app.main import create_app


def _assistant_message(settings) -> tuple[Database, str, str]:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation = database.create_chat_conversation("Correction locale")
    database.append_chat_message(
        conversation_id=conversation["id"],
        role="user",
        content="Question scientifique",
    )
    database.append_chat_message(
        conversation_id=conversation["id"],
        role="assistant",
        content="Réponse scientifique locale.",
    )
    stored = database.chat_conversation(conversation["id"])
    assert stored is not None
    return database, conversation["id"], stored["messages"][1]["id"]


def test_expert_correction_submission_is_private_and_idempotent(settings) -> None:
    database, _conversation_id, message_id = _assistant_message(settings)
    client_request_id = str(uuid4())
    payload = {
        "client_request_id": client_request_id,
        "problem": "La réponse ne distingue pas assez clairement la méthode.",
        "proposed_correction": "Préciser la distinction entre observation et recommandation.",
        "scope": "this_answer",
    }

    with TestClient(create_app(settings)) as client:
        preflight = client.options(
            "/api/expert-memory/corrections/00000000-0000-0000-0000-000000000000",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PATCH",
            },
        )
        created = client.post(
            f"/api/chatbot/messages/{message_id}/expert-corrections", json=payload
        )
        replay = client.post(f"/api/chatbot/messages/{message_id}/expert-corrections", json=payload)
        listed = client.get("/api/expert-memory/corrections", params={"limit": 1})
        detail = client.get(f"/api/expert-memory/corrections/{created.json()['id']}")
        updated = client.patch(
            f"/api/expert-memory/corrections/{created.json()['id']}",
            json={
                "expected_revision": 1,
                "client_request_id": str(uuid4()),
                "problem": "La réponse doit expliciter sa portée méthodologique.",
                "proposed_correction": "Ajouter une phrase qui borne l'interprétation.",
                "scope": "this_answer",
            },
        )
        update_replay = client.patch(
            f"/api/expert-memory/corrections/{created.json()['id']}",
            json={
                "expected_revision": 1,
                "client_request_id": updated.json()["payload"]["client_request_id"],
                "problem": "La réponse doit expliciter sa portée méthodologique.",
                "proposed_correction": "Ajouter une phrase qui borne l'interprétation.",
                "scope": "this_answer",
            },
        )
        stale = client.patch(
            f"/api/expert-memory/corrections/{created.json()['id']}",
            json={
                "expected_revision": 1,
                "client_request_id": str(uuid4()),
                "problem": "Une modification concurrente est obsolète.",
                "proposed_correction": "Ne pas appliquer cette modification obsolète.",
                "scope": "this_answer",
            },
        )
        conflict = client.post(
            f"/api/chatbot/messages/{message_id}/expert-corrections",
            json={**payload, "problem": "Une autre correction incompatible est proposée."},
        )

    assert created.status_code == 201
    assert preflight.status_code == 200
    assert "PATCH" in preflight.headers["access-control-allow-methods"]
    assert created.json()["status"] == "diagnosis_incomplete"
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]
    assert listed.status_code == 200
    assert listed.json()["corrections"][0]["id"] == created.json()["id"]
    assert detail.status_code == 200
    assert detail.json()["payload"]["problem"] == payload["problem"]
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert update_replay.status_code == 200
    assert update_replay.json()["revision"] == 2
    assert stale.status_code == 409
    assert conflict.status_code == 409
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM expert_corrections").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM expert_correction_revisions").fetchone()[0]
            == 2
        )


def test_expert_diagnosis_job_is_queued_without_a_conversation_message(settings) -> None:
    database, _conversation_id, message_id = _assistant_message(settings)
    client_request_id = str(uuid4())
    with TestClient(create_app(settings)) as client:
        created = client.post(
            f"/api/chatbot/messages/{message_id}/expert-corrections",
            json={
                "client_request_id": str(uuid4()),
                "problem": "La trace doit être examinée.",
                "proposed_correction": "Relancer le diagnostic local.",
                "scope": "this_answer",
            },
        )
        before = len(database.chat_conversation(_conversation_id)["messages"])
        queued = client.post(
            f"/api/expert-memory/corrections/{created.json()['id']}/diagnosis-jobs",
            json={
                "client_request_id": client_request_id,
                "expected_revision": 1,
                "max_llm_requests": 0,
            },
        )

    assert queued.status_code == 202
    assert queued.json()["type"] == "expert_improvement"
    assert len(database.chat_conversation(_conversation_id)["messages"]) == before


def test_expert_candidate_review_projection_is_bounded(settings) -> None:
    _database, _conversation_id, _message_id = _assistant_message(settings)

    with TestClient(create_app(settings)) as client:
        listed = client.get("/api/expert-memory/candidates", params={"state": "awaiting_review"})
        invalid = client.get("/api/expert-memory/candidates", params={"state": "unknown"})

    assert listed.status_code == 200
    assert listed.json() == {"candidates": []}
    assert invalid.status_code == 422


def test_expert_activation_routes_require_administrator_profile(settings, monkeypatch) -> None:
    monkeypatch.delenv(LOCAL_PROFILE_ENV, raising=False)
    with TestClient(create_app(settings)) as client:
        candidate_review = client.post(
            f"/api/expert-memory/candidates/{uuid4()}/reviews",
            json={
                "client_request_id": str(uuid4()),
                "candidate_sha256": "a" * 64,
                "evaluation_sha256": "b" * 64,
                "decision": "approve",
                "reviewer_label": "reviewer",
                "reason": "Décision de test suffisamment justifiée.",
            },
        )
        candidate_activation = client.post(
            f"/api/expert-memory/candidates/{uuid4()}/activation",
            json={
                "client_request_id": str(uuid4()),
                "review_id": str(uuid4()),
                "candidate_sha256": "a" * 64,
                "evaluation_sha256": "b" * 64,
                "expected_active_generation": 0,
            },
        )
        distribution_activation = client.post(
            f"/api/expert-memory/distributions/{uuid4()}/activation",
            json={
                "client_request_id": str(uuid4()),
                "expected_active_generation": 0,
                "expected_active_release_id": None,
            },
        )
        release_rollback = client.post(
            f"/api/expert-memory/releases/{uuid4()}/rollback",
            json={
                "client_request_id": str(uuid4()),
                "target_release_id": str(uuid4()),
                "target_release_sha256": "d" * 64,
                "expected_active_generation": 0,
                "reason": "Rollback de test suffisamment justifié.",
            },
        )
        candidate_job = client.post(
            f"/api/expert-memory/diagnoses/{uuid4()}/candidate-jobs",
            json={
                "client_request_id": str(uuid4()),
                "max_llm_requests": 0,
                "patch": {
                    "base_release_id": str(uuid4()),
                    "diagnosis_sha256": "a" * 64,
                    "operations": [
                        {
                            "operation": "retire_item",
                            "item_id": "taxonomy.test",
                            "expected_sha256": "c" * 64,
                            "justification": "Justification suffisamment longue.",
                        }
                    ],
                },
            },
        )
        evaluation_job = client.post(
            f"/api/expert-memory/candidates/{uuid4()}/evaluation-jobs",
            json={
                "client_request_id": str(uuid4()),
                "base_state_path": "evaluations/base/state.json",
                "candidate_state_path": "evaluations/candidate/state.json",
                "max_llm_requests": 0,
            },
        )

    assert candidate_review.status_code == 403
    assert candidate_activation.status_code == 403
    assert distribution_activation.status_code == 403
    assert release_rollback.status_code == 403
    assert candidate_job.status_code == 403
    assert evaluation_job.status_code == 403


def test_expert_activation_domain_refusal_is_not_an_internal_error(settings, monkeypatch) -> None:
    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    Database(settings.paths.database_path).initialize()
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/expert-memory/candidates/{uuid4()}/activation",
            json={
                "client_request_id": str(uuid4()),
                "review_id": str(uuid4()),
                "candidate_sha256": "a" * 64,
                "evaluation_sha256": "b" * 64,
                "expected_active_generation": 0,
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "candidate_or_review_not_found"


def test_missing_evaluation_files_do_not_change_candidate_state(
    settings, expert_package_payload, monkeypatch
) -> None:
    from tests.test_expert_review import _prepared_candidate

    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    database, candidate_id, _release, evaluation_id, _report = _prepared_candidate(
        settings, expert_package_payload
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE expert_candidates SET state = 'structurally_valid' WHERE id = ?",
            (str(candidate_id),),
        )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/api/expert-memory/evaluations/{evaluation_id}/evaluation-jobs",
            json={
                "client_request_id": str(uuid4()),
                "base_state_path": "evaluations/base/state.json",
                "candidate_state_path": "evaluations/candidate/state.json",
                "max_llm_requests": 0,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "evaluation_campaign_not_completed"
    with database.connect() as connection:
        state = connection.execute(
            "SELECT state FROM expert_candidates WHERE id = ?", (str(candidate_id),)
        ).fetchone()[0]
    assert state == "structurally_valid"
