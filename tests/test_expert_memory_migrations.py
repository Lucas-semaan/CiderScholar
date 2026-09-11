from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from app.database import migrations
from app.database.sqlite import Database
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository, KnowledgeRepositoryError

EXPERT_TABLES = {
    "expert_releases",
    "expert_release_items",
    "expert_release_dependencies",
    "expert_active_release",
    "expert_release_events",
}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _create_version_34(database: Database, monkeypatch) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(migrations, "CURRENT_SCHEMA_VERSION", 34)
        database.initialize()


def test_new_database_and_second_initialization_preserve_import(
    tmp_path, expert_package_payload
) -> None:
    database = Database(tmp_path / "fresh.sqlite3")
    database.initialize()
    repository = KnowledgeRepository(database)
    package = KnowledgePackage.model_validate(expert_package_payload)
    stored = repository.import_candidate(package)
    database.initialize()
    assert repository.load_release(stored.id)[1] == package
    with database.read_session() as session:
        connection = session.connection
        assert _tables(connection) >= EXPERT_TABLES
        assert (
            connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            == migrations.CURRENT_SCHEMA_VERSION
        )
        assert connection.execute("SELECT COUNT(*) FROM expert_releases").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM expert_active_release").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_34_preserves_scientific_data_fts_and_conversations(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "existing.sqlite3")
    _create_version_34(database, monkeypatch)
    conversation = database.create_chat_conversation("Existing synthetic conversation")
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO articles(id, sha256, title, pdf_path) VALUES (?, ?, ?, ?)",
            ("source", "a" * 64, "Existing synthetic article", "synthetic.pdf"),
        )
        connection.execute(
            """INSERT INTO chunks(article_id, page_start, page_end, chunk_index, text, token_count)
               VALUES ('source', 2, 2, 0, 'Synthetic fermentation evidence.', 3)"""
        )
        preserved = {
            name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
            for name in ("articles", "chunks", "chunks_fts", "chat_conversations")
        }
        assert not EXPERT_TABLES & _tables(connection)
    database.initialize()
    with database.read_session() as session:
        for name, rows in preserved.items():
            assert [
                tuple(row) for row in session.connection.execute(f"SELECT * FROM {name}")
            ] == rows
        assert session.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert database.lexical_search("fermentation")[0]["article_id"] == "source"
    assert (
        database.chat_conversation(conversation["id"])["title"] == "Existing synthetic conversation"
    )


def test_interrupted_migration_rolls_back_schema_and_version(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "interrupted.sqlite3")
    _create_version_34(database, monkeypatch)
    with closing(database.connect()) as connection:
        connection.set_authorizer(
            lambda operation, table, *_: (
                sqlite3.SQLITE_DENY
                if operation == sqlite3.SQLITE_INSERT and table == "schema_version"
                else sqlite3.SQLITE_OK
            )
        )
        with pytest.raises(sqlite3.DatabaseError):
            migrations.ensure_current(connection)
        # Closing an interrupted connection must undo both DDL and version writes.
    with database.read_session() as session:
        assert (
            session.connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            == 34
        )
        assert not EXPERT_TABLES & _tables(session.connection)
    database.initialize()
    with database.read_session() as session:
        assert _tables(session.connection) >= EXPERT_TABLES
        assert session.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repository_cannot_migrate_version_34_implicitly(
    tmp_path, monkeypatch, expert_package_payload
) -> None:
    database = Database(tmp_path / "old.sqlite3")
    _create_version_34(database, monkeypatch)
    before = database.path.read_bytes()
    package = KnowledgePackage.model_validate(expert_package_payload)
    with pytest.raises(KnowledgeRepositoryError, match="^migration_required_before_import$"):
        KnowledgeRepository(database).import_candidate(package)
    assert database.path.read_bytes() == before


def test_foreign_keys_and_singleton_enforced(tmp_path, expert_package_payload) -> None:
    database = Database(tmp_path / "constraints.sqlite3")
    database.initialize()
    repository = KnowledgeRepository(database)
    stored = repository.import_candidate(KnowledgePackage.model_validate(expert_package_payload))
    statements = [
        ("INSERT INTO expert_active_release(singleton) VALUES (2)", ()),
        ("UPDATE expert_active_release SET generation = -1", ()),
        ("UPDATE expert_active_release SET release_id = 'missing'", ()),
        (
            """INSERT INTO expert_release_dependencies(release_id, item_id, dependency_id)
               VALUES ('missing', 'taxonomy.synthetic', 'taxonomy.missing')""",
            (),
        ),
        (
            """INSERT INTO expert_release_events(id, to_release_id, event_type, reason, created_at)
               VALUES ('event', 'missing', 'imported', 'test', '2026-09-07')""",
            (),
        ),
        ("UPDATE expert_releases SET state = 'active' WHERE id = ?", (str(stored.id),)),
    ]
    for statement, parameters in statements:
        with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
            connection.execute(statement, parameters)
    with database.read_session() as session:
        assert session.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_dependency_foreign_keys_cannot_cross_releases(tmp_path) -> None:
    database = Database(tmp_path / "composite.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        # Deliberately unsealed SQL fixtures isolate the FK check from import guards.
        for release_id, item_id, digest in (
            ("first", "taxonomy.consumer", "a" * 64),
            ("second", "taxonomy.target", "b" * 64),
        ):
            connection.execute(
                """INSERT INTO expert_releases(id, package_sha256, schema_version,
                       app_min_version, created_at, manifest_json)
                   VALUES (?, ?, 1, '0.2.11', '2026-09-07', '{}')""",
                (release_id, digest),
            )
            connection.execute(
                """INSERT INTO expert_release_items(release_id, item_id, revision, kind,
                       content_sha256, payload_json) VALUES (?, ?, 1, 'taxonomy', ?, '{}')""",
                (release_id, item_id, digest),
            )
    for item_id, dependency in (
        ("taxonomy.consumer", "taxonomy.target"),
        ("taxonomy.target", "taxonomy.consumer"),
    ):
        with (
            pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"),
            database.transaction() as connection,
        ):
            connection.execute(
                """INSERT INTO expert_release_dependencies(release_id, item_id, dependency_id)
                   VALUES ('first', ?, ?)""",
                (item_id, dependency),
            )


def test_unsupported_schema_cannot_be_imported(tmp_path, expert_package_payload) -> None:
    database = Database(tmp_path / "future.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO schema_version(version) VALUES (999)")
    before = database.path.read_bytes()
    with pytest.raises(KnowledgeRepositoryError, match="^incompatible_schema_version$"):
        KnowledgeRepository(database).import_candidate(
            KnowledgePackage.model_validate(expert_package_payload)
        )
    assert database.path.read_bytes() == before
