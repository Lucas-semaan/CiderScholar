from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from uuid import uuid4

import pytest

from app.database.sqlite import Database
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository, KnowledgeRepositoryError


@pytest.fixture
def repository(tmp_path) -> KnowledgeRepository:
    database = Database(tmp_path / "application.sqlite3")
    database.initialize()
    return KnowledgeRepository(database)


@pytest.fixture
def package(expert_package_payload) -> KnowledgePackage:
    related = deepcopy(expert_package_payload["items"][0])
    related.update(id="taxonomy.related", depends_on=["taxonomy.synthetic"])
    expert_package_payload["items"].append(related)
    return KnowledgePackage.model_validate(expert_package_payload)


def _counts(repository: KnowledgeRepository) -> tuple[int, ...]:
    with repository.database.read_session() as session:
        return tuple(
            session.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "expert_releases",
                "expert_release_items",
                "expert_release_dependencies",
                "expert_release_events",
            )
        )


def test_import_is_idempotent_verified_and_never_activates(repository, package) -> None:
    before = repository.resolve_active_release()
    stored = repository.import_candidate(package)
    repeated = repository.import_candidate(package)
    assert repeated == stored
    assert stored.state == "candidate"
    assert stored.base_release_id is None
    assert repository.load_release(stored.id) == (stored, package)
    assert repository.resolve_active_release() == before
    assert before.release_id is None
    assert before.generation == 0
    assert _counts(repository) == (1, 2, 1, 1)
    with repository.database.read_session() as session:
        assert session.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert session.connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
        assert session.connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0


def test_multiple_process_equivalent_imports_create_one_release(repository, package) -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        releases = list(executor.map(lambda _: repository.import_candidate(package), range(8)))
    assert all(release == releases[0] for release in releases)
    assert _counts(repository) == (1, 2, 1, 1)
    assert repository.resolve_active_release().generation == 0


def test_base_identity_is_explicit_and_idempotent(repository, package) -> None:
    missing = uuid4()
    with pytest.raises(KnowledgeRepositoryError, match="^base_release_missing$"):
        repository.import_candidate(package, base_release_id=missing)
    assert _counts(repository) == (0, 0, 0, 0)
    base = repository.import_candidate(package)
    revised = package.model_copy(update={"version": "1.0.1"})
    child = repository.import_candidate(revised, base_release_id=base.id)
    assert child.base_release_id == base.id
    assert repository.import_candidate(revised, base_release_id=base.id) == child
    with pytest.raises(KnowledgeRepositoryError, match="^candidate_base_conflict$"):
        repository.import_candidate(revised)
    assert _counts(repository) == (2, 4, 2, 2)


@pytest.mark.parametrize("mutation", ["missing_dependency", "cycle", "invalid_model"])
def test_bad_package_is_rejected_before_any_storage(repository, package, mutation) -> None:
    payload = package.model_dump(mode="json")
    if mutation == "missing_dependency":
        payload["items"][0]["depends_on"] = ["taxonomy.missing"]
        invalid = KnowledgePackage.model_validate(payload)
    elif mutation == "cycle":
        payload["items"][1]["depends_on"] = ["taxonomy.related"]
        invalid = KnowledgePackage.model_validate(payload)
    else:
        invalid = package.model_copy(update={"minimum_schema_version": True})
    with pytest.raises(KnowledgeRepositoryError, match="^invalid_package$"):
        repository.import_candidate(invalid)
    assert _counts(repository) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("minimum_app_version", "999.0.0", "incompatible_app_version"),
        ("minimum_schema_version", 999, "incompatible_schema_version"),
    ],
)
def test_incompatible_package_cannot_be_imported(repository, package, field, value, code) -> None:
    with pytest.raises(KnowledgeRepositoryError, match=f"^{code}$"):
        repository.import_candidate(package.model_copy(update={field: value}))
    assert _counts(repository) == (0, 0, 0, 0)


def test_interrupted_import_rolls_back_all_rows_and_can_retry(
    repository, package, monkeypatch
) -> None:
    original_connect = repository.database.connect

    def fail_event_insert():
        connection = original_connect()
        connection.set_authorizer(
            lambda operation, table, *_: (
                sqlite3.SQLITE_DENY
                if operation == sqlite3.SQLITE_INSERT and table == "expert_release_events"
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    with monkeypatch.context() as scoped:
        scoped.setattr(repository.database, "connect", fail_event_insert)
        with pytest.raises(sqlite3.DatabaseError):
            repository.import_candidate(package)
    assert _counts(repository) == (0, 0, 0, 0)
    assert repository.resolve_active_release().generation == 0
    stored = repository.import_candidate(package)
    assert repository.load_release(stored.id)[1] == package


def test_failed_readback_prevents_commit(repository, package, monkeypatch) -> None:
    def reject_readback(*_):
        raise KnowledgeRepositoryError("stored_package_mismatch")

    with monkeypatch.context() as scoped:
        scoped.setattr(repository, "_load_package", reject_readback)
        with pytest.raises(KnowledgeRepositoryError, match="stored_package_mismatch"):
            repository.import_candidate(package)
    assert _counts(repository) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE expert_releases SET manifest_json = '{}'",
        "UPDATE expert_release_items SET revision = revision + 1",
        "DELETE FROM expert_release_items",
        "UPDATE expert_release_dependencies SET dependency_id = item_id",
        "DELETE FROM expert_release_dependencies",
        "UPDATE expert_release_events SET reason = 'changed'",
        "DELETE FROM expert_release_events",
        "DELETE FROM expert_releases",
        """INSERT INTO expert_release_items
           SELECT release_id, 'taxonomy.extra', revision, kind, content_sha256, payload_json
           FROM expert_release_items LIMIT 1""",
        """INSERT INTO expert_release_dependencies
           SELECT release_id, dependency_id, item_id FROM expert_release_dependencies""",
    ],
)
def test_sealed_content_and_import_audit_are_immutable(repository, package, statement) -> None:
    stored = repository.import_candidate(package)
    with pytest.raises(sqlite3.IntegrityError), repository.database.transaction() as connection:
        connection.execute(statement)
    assert repository.load_release(stored.id)[1] == package
    assert _counts(repository) == (1, 2, 1, 1)


@pytest.mark.parametrize(
    ("trigger", "statement", "code"),
    [
        (
            "expert_item_immutable",
            "UPDATE expert_release_items SET revision = revision + 1",
            "stored_item_mismatch",
        ),
        (
            "expert_release_content_immutable",
            "UPDATE expert_releases SET app_min_version = '0.0.0'",
            "stored_package_mismatch",
        ),
        (
            "expert_release_content_immutable",
            "UPDATE expert_releases SET manifest_json = '[]'",
            "invalid_stored_package",
        ),
        (
            "expert_dependency_no_delete",
            "DELETE FROM expert_release_dependencies",
            "stored_graph_mismatch",
        ),
        (
            "expert_item_no_delete",
            "DELETE FROM expert_release_items WHERE item_id = 'taxonomy.related'",
            "stored_package_mismatch",
        ),
    ],
)
def test_offline_tampering_is_detected_on_read_and_reimport(
    repository, package, trigger, statement, code
) -> None:
    stored = repository.import_candidate(package)
    # Simulate corruption by a process that bypassed the normal write guards.
    with closing(sqlite3.connect(repository.database.path)) as connection, connection:
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(statement)
    with pytest.raises(KnowledgeRepositoryError, match=f"^{code}$"):
        repository.load_release(stored.id)
    with pytest.raises(KnowledgeRepositoryError, match=f"^{code}$"):
        repository.import_candidate(package)


def test_missing_and_ineligible_active_releases_fail_closed(repository, package) -> None:
    with pytest.raises(KnowledgeRepositoryError, match="^release_missing$"):
        repository.load_release(uuid4())
    stored = repository.import_candidate(package)
    with repository.database.transaction() as connection:
        connection.execute(
            "UPDATE expert_active_release SET release_id = ? WHERE singleton = 1",
            (str(stored.id),),
        )
    with pytest.raises(KnowledgeRepositoryError, match="^active_release_ineligible$"):
        repository.resolve_active_release()


def test_absent_database_is_not_created_by_repository(tmp_path, package) -> None:
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        KnowledgeRepository(Database(path)).import_candidate(package)
    assert not path.exists()


def test_corrupted_base_cannot_seed_a_new_candidate(repository, package) -> None:
    base = repository.import_candidate(package)
    with repository.database.transaction() as connection:
        connection.execute("DROP TRIGGER expert_item_immutable")
        connection.execute("UPDATE expert_release_items SET revision = revision + 1")
    with pytest.raises(KnowledgeRepositoryError, match="^stored_item_mismatch$"):
        repository.import_candidate(
            package.model_copy(update={"version": "1.0.1"}), base_release_id=base.id
        )
    assert _counts(repository) == (1, 2, 1, 1)
