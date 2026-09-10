from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from app.config import load_settings
from app.database.sqlite import Database
from app.knowledge.repository import KnowledgeRepository
from scripts.import_expert_knowledge import main


def _config(tmp_path: Path, *, shared: bool = False) -> Path:
    path = tmp_path / "config.yaml"
    database_path = (
        "data/common/database/science_rag.sqlite3" if shared else "data/application.sqlite3"
    )
    path.write_text(yaml.safe_dump({"paths": {"database_path": database_path}}), encoding="utf-8")
    return path


def _argv(expert_knowledge_dir: Path, config: Path, *, apply: bool = False) -> list[str]:
    return [
        "--knowledge-dir",
        str(expert_knowledge_dir),
        "--source-root",
        str(expert_knowledge_dir.parent),
        "--config",
        str(config),
        *(["--apply"] if apply else []),
    ]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_dry_run_creates_no_database_or_data_directories(
    tmp_path, expert_knowledge_dir, capsys
) -> None:
    config = _config(tmp_path)
    before = _snapshot(tmp_path)
    assert main(_argv(expert_knowledge_dir, config)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False
    assert report["activated"] is False
    assert report["item_count"] == 1
    assert len(report["package_sha256"]) == 64
    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "data").exists()


def test_dry_run_does_not_open_or_migrate_existing_database(
    tmp_path, expert_knowledge_dir, capsys, monkeypatch
) -> None:
    config = _config(tmp_path)
    database = Database(load_settings(config).paths.database_path)
    database.path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(database.path)) as connection, connection:
        connection.executescript(
            "CREATE TABLE schema_version(version INTEGER); INSERT INTO schema_version VALUES(34);"
        )
    before = _snapshot(tmp_path)

    def forbidden(*_, **__):
        raise AssertionError("dry-run must not open SQLite")

    monkeypatch.setattr(Database, "read_session", forbidden)
    monkeypatch.setattr(Database, "initialize", forbidden)
    monkeypatch.setattr(Database, "connect", forbidden)
    assert main(_argv(expert_knowledge_dir, config)) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("shared", [False, True])
def test_apply_targets_only_application_database_and_never_activates(
    tmp_path, expert_knowledge_dir, capsys, shared
) -> None:
    config = _config(tmp_path, shared=shared)
    settings = load_settings(config)
    database = Database(settings.paths.database_path)
    database.initialize()
    corpus = Database(settings.paths.common_database_path)
    corpus.initialize()
    with corpus.transaction() as connection:
        connection.execute(
            """INSERT INTO articles(id, sha256, title, pdf_path)
               VALUES ('existing', ?, 'Synthetic', 'test.pdf')""",
            ("a" * 64,),
        )
    with corpus.read_session() as session:
        corpus_rows = [tuple(row) for row in session.connection.execute("SELECT * FROM articles")]
    for _ in range(2):
        assert main(_argv(expert_knowledge_dir, config, apply=True)) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["applied"] is True
        assert report["activated"] is False
        assert report["state"] == "candidate"
    with database.read_session() as session:
        assert session.connection.execute("SELECT COUNT(*) FROM expert_releases").fetchone()[0] == 1
        assert (
            session.connection.execute("SELECT COUNT(*) FROM expert_release_events").fetchone()[0]
            == 1
        )
    with corpus.read_session() as session:
        assert [
            tuple(row) for row in session.connection.execute("SELECT * FROM articles")
        ] == corpus_rows
        assert session.connection.execute("SELECT COUNT(*) FROM expert_releases").fetchone()[
            0
        ] == int(shared)
    assert KnowledgeRepository(database).resolve_active_release().release_id is None


@pytest.mark.parametrize("existing", [False, True])
def test_apply_refuses_absent_or_unmigrated_database_without_mutation(
    tmp_path, expert_knowledge_dir, capsys, existing
) -> None:
    config = _config(tmp_path)
    database = Database(load_settings(config).paths.database_path)
    if existing:
        database.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database.path)) as connection, connection:
            connection.executescript(
                "CREATE TABLE schema_version(version INTEGER); "
                "INSERT INTO schema_version VALUES(34);"
            )
    before = _snapshot(tmp_path)
    assert main(_argv(expert_knowledge_dir, config, apply=True)) == (1 if existing else 2)
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False
    assert report["code"] == (
        "migration_required_before_import" if existing else "import_unavailable"
    )
    assert _snapshot(tmp_path) == before


def test_invalid_package_reports_no_source_text_and_never_writes(
    tmp_path, expert_knowledge_dir, capsys
) -> None:
    config = _config(tmp_path)
    marker = "private_input_must_not_be_reported"
    (expert_knowledge_dir / "synthetic.md").write_text(marker, encoding="utf-8")
    before = _snapshot(tmp_path)
    assert main(_argv(expert_knowledge_dir, config, apply=True)) == 1
    output = capsys.readouterr().out
    assert marker not in output
    assert json.loads(output)["applied"] is False
    assert _snapshot(tmp_path) == before
