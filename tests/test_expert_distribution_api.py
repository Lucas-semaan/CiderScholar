from __future__ import annotations

from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.config import PathConfig
from app.knowledge.package import (
    build_approved_expert_memory_package,
    import_staged_expert_memory_package,
    stage_expert_memory_package,
)
from app.main import create_app
from tests.test_expert_memory_package import _approve_candidate


def test_distribution_api_exposes_explicit_local_transitions(
    settings, expert_package_payload, tmp_path, monkeypatch
) -> None:
    from app.corpora import LOCAL_PROFILE_ENV

    monkeypatch.setenv(LOCAL_PROFILE_ENV, "admin")
    database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        database, candidate_id, output_root=tmp_path / "packages"
    )
    staged = stage_expert_memory_package(built.package_directory, staging_root=tmp_path / "staging")
    target_data = tmp_path / "target-data"
    target_settings = settings.model_copy(
        deep=True,
        update={
            "paths": PathConfig(
                data_dir=target_data,
                pdf_dir=target_data / "pdf",
                extracted_dir=target_data / "extracted",
                qdrant_dir=target_data / "qdrant",
                models_dir=target_data / "models",
                database_path=target_data / "database" / "target.sqlite3",
                cache_dir=target_data / "cache",
                exports_dir=target_data / "exports",
            )
        },
    )
    target_settings.paths.create()
    from app.database.sqlite import Database

    target_database = Database(target_settings.paths.database_path)
    target_database.initialize()
    imported = import_staged_expert_memory_package(target_database, staged)
    with target_database.connect() as connection:
        distribution_id = UUID(
            str(
                connection.execute(
                    "SELECT id FROM expert_memory_distributions WHERE release_id = ?",
                    (str(imported.id),),
                ).fetchone()[0]
            )
        )

    with TestClient(create_app(target_settings)) as client:
        listed = client.get("/api/expert-memory/distributions")
        releases = client.get("/api/expert-memory/releases")
        invalid_releases = client.get(
            "/api/expert-memory/releases", params={"cursor": "not-a-cursor"}
        )
        proposed = client.post(f"/api/expert-memory/distributions/{distribution_id}/proposal")
        approved = client.post(
            f"/api/expert-memory/distributions/{distribution_id}/approval",
            json={
                "reviewer_label": "local-admin",
                "reason": "Paquet vérifié localement avant activation.",
            },
        )
        blocked = client.post(
            f"/api/expert-memory/distributions/{distribution_id}/activation",
            json={
                "client_request_id": str(uuid4()),
                "expected_active_generation": 99,
                "expected_active_release_id": None,
            },
        )
        detail = client.get(f"/api/expert-memory/distributions/{distribution_id}")

    assert listed.status_code == 200
    assert listed.json()["distributions"][0]["state"] == "imported"
    assert releases.status_code == 200
    assert releases.json()["active_release_id"] is None
    assert releases.json()["releases"][0]["active"] is False
    assert invalid_releases.status_code == 422
    assert invalid_releases.json()["detail"]["code"] == "release_cursor_invalid"
    assert proposed.status_code == 200
    assert proposed.json()["state"] == "proposed"
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "active_generation_conflict"
    assert detail.status_code == 200
    assert detail.json()["id"] == str(distribution_id)
