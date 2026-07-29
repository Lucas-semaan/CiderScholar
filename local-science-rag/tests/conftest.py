from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PathConfig, Settings
from app.memory import MemoryGuard, MemorySnapshot


@pytest.fixture(autouse=True)
def stable_test_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep functional tests independent from unrelated host RAM fluctuations."""

    monkeypatch.setattr(
        MemoryGuard,
        "snapshot",
        lambda _self: MemorySnapshot(
            process_rss_gb=0.25,
            system_used_gb=4.0,
            system_available_gb=8.0,
        ),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    configured = Settings(
        paths=PathConfig(
            data_dir=data,
            pdf_dir=data / "pdf",
            extracted_dir=data / "extracted",
            qdrant_dir=data / "qdrant",
            models_dir=data / "models",
            database_path=data / "database" / "test.sqlite3",
            cache_dir=data / "cache",
            exports_dir=data / "exports",
        )
    )
    configured.paths.create()
    return configured
