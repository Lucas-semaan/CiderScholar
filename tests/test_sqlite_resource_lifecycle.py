from __future__ import annotations

import hashlib
import sqlite3

import pytest

from app.database.sqlite import Database
from app.diagnostics import _corpus_check
from scripts.audit_local_pdf_metadata import apply_reviewed_audit


@pytest.fixture
def tracked_connections(monkeypatch):
    connections = []
    connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    yield connections
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("initialized", [True, False])
def test_corpus_diagnostic_closes_connections_on_success_and_sql_errors(
    settings, tracked_connections, initialized
) -> None:
    if initialized:
        Database(settings.paths.common_database_path).initialize()
    _corpus_check(settings)


@pytest.mark.parametrize("scenario", ["no_changes", "stale_audit", "invalid_candidate"])
def test_metadata_audit_closes_readers_backups_and_transactions(
    settings, tmp_path, tracked_connections, scenario
) -> None:
    path = settings.paths.common_database_path
    Database(path).initialize()
    audit = {
        "database_fingerprint": (
            "stale" if scenario == "stale_audit" else hashlib.sha256(b"").hexdigest()
        ),
        "candidates": [{}] if scenario == "invalid_candidate" else [],
    }
    options = {"database_path": path, "audit": audit, "backup_dir": tmp_path / "backups"}
    if scenario == "no_changes":
        assert apply_reviewed_audit(**options)["applied"] == []
    else:
        with pytest.raises(RuntimeError if scenario == "stale_audit" else ValueError):
            apply_reviewed_audit(**options)
