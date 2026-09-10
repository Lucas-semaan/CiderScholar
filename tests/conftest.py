from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from app.config import PathConfig, Settings
from app.memory import MemoryGuard, MemorySnapshot


@pytest.fixture
def expert_package_payload() -> dict:
    """A synthetic vocabulary package; it never refers to the user's corpus."""

    return {
        "schema_version": 1,
        "package_id": "test.synthetic",
        "version": "1.0.0",
        "minimum_app_version": "0.2.11",
        "minimum_schema_version": 34,
        "items": [
            {
                "schema_version": 1,
                "id": "taxonomy.synthetic",
                "revision": 1,
                "kind": "taxonomy",
                "title": "Synthetic vocabulary",
                "language": "multilingual",
                "authority": "proposal",
                "stage": "planning",
                "depends_on": [],
                "body": "",
                "provenance": [
                    {
                        "document": "docs/METHOD.md",
                        "section": "1",
                        "revision_sha256": sha256(b"Synthetic method.\n").hexdigest(),
                    }
                ],
                "data": {
                    "canonical_term": "synthetic process",
                    "aliases_fr": ["procédé fictif"],
                    "aliases_en": ["test process"],
                    "ambiguity_terms": [],
                    "facet_kind": "process",
                },
            }
        ],
    }


@pytest.fixture
def expert_knowledge_dir(tmp_path: Path, expert_package_payload) -> Path:
    root = tmp_path / "knowledge"
    root.mkdir()
    method_dir = tmp_path / "docs"
    method_dir.mkdir()
    (method_dir / "METHOD.md").write_bytes(b"Synthetic method.\n")
    payload = expert_package_payload
    item = {**payload["items"][0]}
    body = item.pop("body")
    (root / "synthetic.md").write_text(
        "---\n" + yaml.safe_dump(item, allow_unicode=True, sort_keys=True) + "---\n" + body,
        encoding="utf-8",
    )
    metadata = {key: value for key, value in payload.items() if key != "items"}
    (root / "package.json").write_text(
        json.dumps({**metadata, "files": ["synthetic.md"]}), encoding="utf-8"
    )
    return root


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
