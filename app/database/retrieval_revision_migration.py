"""Transactional corpus revision for constant-time, fail-closed cache invalidation."""

import sqlite3


def add_retrieval_revision(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE retrieval_revision (id INTEGER PRIMARY KEY CHECK(id=1), "
        "revision INTEGER NOT NULL, token TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO retrieval_revision VALUES (1, 0, lower(hex(randomblob(32))))")
    # Include provenance and all tables that can change scientific source eligibility.
    tables = (
        "articles",
        "chunks",
        "bibliographic_records",
        "bibliographic_record_sources",
        "document_elements",
        "article_authors",
        "authors",
        "article_identifiers",
        "article_retrieval_exclusions",
        "document_table_cells",
        "document_element_relations",
    )
    existing = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table in tables:
        if table not in existing:
            continue
        for event in ("INSERT", "UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER retrieval_revision_{table}_{event.lower()} "
                f"AFTER {event} ON {table} BEGIN "
                "UPDATE retrieval_revision SET revision=revision+1, "
                "token=lower(hex(randomblob(32))) WHERE id=1; END"
            )
