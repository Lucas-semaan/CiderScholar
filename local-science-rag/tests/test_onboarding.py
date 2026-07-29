from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database.sqlite import Database
from app.desktop.model_integrity import write_model_manifest
from app.desktop.onboarding import OnboardingError, onboarding_status, select_memory_profile
from app.ingestion.embeddings import local_model_path
from app.main import create_app
from app.memory_profiles import EIGHT_GB_PROFILE, MemoryProfileName, apply_memory_profile


def _packaged_config(settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "UserData" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "paths:\n"
        f"  data_dir: {settings.paths.data_dir.as_posix()}\n"
        f"  models_dir: {settings.paths.models_dir.as_posix()}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CIDERSCHOLAR_CONFIG_PATH", str(config))
    return config


def test_first_launch_status_is_derived_from_verified_resources(settings) -> None:
    model = local_model_path(settings)
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    write_model_manifest(model, settings.embeddings.model_name)

    status = onboarding_status(settings)

    assert status.model_ready is True
    assert status.sharepoint_ready is False
    assert status.corpus_ready is False
    assert status.completed is False


def test_bundled_base_corpus_is_ready_without_sharepoint(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = apply_memory_profile(settings, EIGHT_GB_PROFILE)
    model = local_model_path(configured)
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    write_model_manifest(model, configured.embeddings.model_name)
    Database(configured.paths.common_database_path).initialize()
    qdrant_file = configured.paths.common_qdrant_dir / "collection" / "science_chunks" / "data"
    qdrant_file.mkdir(parents=True)
    (qdrant_file / "storage.sqlite").write_bytes(b"base corpus")
    (configured.paths.common_dir / ".ciderscholar-bundled-corpus").write_text(
        "default-rag\n",
        encoding="ascii",
    )
    monkeypatch.setattr("app.desktop.onboarding.ArgoKeyStore.configured", lambda _self: True)

    status = onboarding_status(configured)

    assert status.sharepoint_ready is False
    assert status.corpus_ready is True
    assert status.installed_corpus_version == "default-rag"
    assert status.completed is True


def test_memory_choice_is_persisted_but_can_be_corrected(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _packaged_config(settings, tmp_path, monkeypatch)

    eight = select_memory_profile(settings, MemoryProfileName.EIGHT_GB)
    sixteen = select_memory_profile(eight, MemoryProfileName.SIXTEEN_GB)

    assert eight.memory.profile == "8gb"
    assert sixteen.memory.profile == "16gb"
    overrides = config.with_name("config.user.yaml").read_text(encoding="utf-8")
    assert "profile: 16gb" in overrides


def test_onboarding_api_returns_actionable_conflict_for_invalid_sharepoint(
    settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _packaged_config(settings, tmp_path, monkeypatch)
    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/api/onboarding/sharepoint",
            json={"path": str(tmp_path / "missing"), "confirm_unexpected_name": True},
        )

    assert response.status_code == 409
    assert "n'existe pas" in response.json()["detail"]


def test_first_corpus_cannot_start_without_verified_sharepoint(settings) -> None:
    from app.desktop.onboarding import install_first_common_corpus

    with pytest.raises(OnboardingError, match="pas configurée"):
        install_first_common_corpus(settings)
