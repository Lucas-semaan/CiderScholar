"""Additive acquisition and targeted indexing; readiness is verified in SQLite."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from app.config import Settings
from app.database.sqlite import Database
from app.ingestion.embeddings import SentenceTransformerBackend
from app.services.workflows import index_pending_chunks
from app.updates.full_text import FullTextHarvestService
from app.updates.harvest import BibliographicHarvestStore
from app.updates.vector_index import index_bibliographic_abstracts


def ready_content(connection: sqlite3.Connection, record_id: str) -> str | None:
    row = connection.execute(
        "SELECT doi, embedding_status, abstract, relevance_status FROM "
        "bibliographic_records WHERE id=?",
        (record_id,),
    ).fetchone()
    if row is None or row["relevance_status"] != "accepted":
        return None
    full = connection.execute(
        "SELECT a.id, COUNT(c.id) AS total, "
        "SUM(CASE WHEN c.embedding_status='indexed' THEN 1 ELSE 0 END) AS indexed "
        "FROM articles a JOIN chunks c ON c.article_id=a.id "
        "WHERE a.doi=? COLLATE NOCASE "
        "AND NOT EXISTS (SELECT 1 FROM article_retrieval_exclusions e WHERE e.article_id=a.id) "
        "GROUP BY a.id",
        (row["doi"],),
    ).fetchall()
    if any(item["total"] > 0 and item["total"] == item["indexed"] for item in full):
        return "full_articles"
    if row["embedding_status"] == "indexed" and row["abstract"]:
        return "abstracts_only"
    return None


class WatchContent:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings.model_copy(deep=True)
        self.settings.full_text.enabled = True
        self.database = database

    def process(self, record_id: str, *, acquire: bool) -> str | None:
        self.errors: list[str] = []
        if acquire:
            try:
                _, harvest = FullTextHarvestService(self.settings, self.database).run(
                    record_ids=[record_id],
                    include_slow_fallbacks=False,
                    max_downloads=1,
                    max_native_downloads=1,
                )
                if harvest.failed or harvest.native_failed or harvest.errors:
                    self.errors.append(f"{record_id}: full_text_acquisition_incomplete")
            except Exception as error:
                self.errors.append(f"{record_id}: acquisition_{type(error).__name__}")
        with closing(self.database.connect()) as connection:
            article_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT a.id FROM articles a JOIN bibliographic_records r "
                    "ON a.doi=r.doi COLLATE NOCASE WHERE r.id=?",
                    (record_id,),
                )
            ]
        if article_ids and self.database.chunks_for_embedding(
            limit=1, retry_failed=True, article_ids=article_ids
        ):
            index_pending_chunks(
                self.settings, self.database, article_ids=article_ids, retry_failed=True
            )
        bibliography = BibliographicHarvestStore(self.database)
        if bibliography.pending_abstracts(limit=1, record_ids={record_id}):
            index_bibliographic_abstracts(
                self.settings,
                bibliography,
                SentenceTransformerBackend(self.settings),
                record_ids=[record_id],
                max_batches=1,
            )
        with closing(self.database.connect()) as connection:
            return ready_content(connection, record_id)
