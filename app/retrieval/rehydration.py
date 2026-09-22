"""Rehydrate cached/conversational sources from the current SQLite authority."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord


def rehydrate_records(
    settings: Settings, records: Sequence[ChatEvidenceRecord]
) -> list[ChatEvidenceRecord]:
    result = []
    for scope in CorpusScope:
        candidates = [
            record for record in records if record.scope == scope and record.origin == "local_rag"
        ]
        if not candidates:
            continue
        path = corpus_paths(settings, scope).database_path.resolve()
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            for record in candidates:
                article_id = record.article_id
                if article_id is None and record.record_id.startswith("common:"):
                    article_id = record.record_id.removeprefix("common:")
                if article_id:
                    row = connection.execute(
                        "SELECT * FROM articles WHERE id=? "
                        "AND validation_status IN ('validated','indexed') "
                        "AND id NOT IN (SELECT article_id FROM article_retrieval_exclusions)",
                        (article_id,),
                    ).fetchone()
                else:
                    bibliographic_id = record.record_id.removeprefix("common-abstract:")
                    row = connection.execute(
                        "SELECT * FROM bibliographic_records WHERE id=? "
                        "AND relevance_status='accepted' "
                        "AND (manual_decision IS NULL OR manual_decision!='rejected')",
                        (bibliographic_id,),
                    ).fetchone()
                if row is None or row["title"].strip().casefold() == "fichier local":
                    continue
                passages = []
                if record.evidence_level == "full_text":
                    for passage in record.passages:
                        chunk = connection.execute(
                            """
                            SELECT c.*,
                                   COALESCE(correction.corrected_text, c.text) AS effective_text,
                                   l.locator_kind,
                                   CASE WHEN l.locator_kind = 'structural' THEN NULL
                                        ELSE COALESCE(l.page_start, c.page_start) END
                                     AS locator_page_start,
                                   CASE WHEN l.locator_kind = 'structural' THEN NULL
                                        ELSE COALESCE(l.page_end, c.page_end) END
                                     AS locator_page_end,
                                   l.section_path,
                                   l.paragraph_start, l.paragraph_end,
                                   l.xml_id_start, l.xml_id_end
                            FROM chunks AS c
                            LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                            LEFT JOIN chunk_corrections AS correction
                              ON correction.chunk_id = c.id AND correction.state = 'approved'
                            WHERE c.id=? AND c.article_id=?
                            """,
                            (passage.chunk_id, article_id),
                        ).fetchone()
                        if chunk is not None:
                            passages.append(
                                passage.model_copy(
                                    update={
                                        "text": chunk["effective_text"],
                                        "section": chunk["section"],
                                        "page_start": chunk["locator_page_start"],
                                        "page_end": chunk["locator_page_end"],
                                        "locator_kind": chunk["locator_kind"],
                                        "section_path": chunk["section_path"],
                                        "paragraph_start": chunk["paragraph_start"],
                                        "paragraph_end": chunk["paragraph_end"],
                                        "xml_id_start": chunk["xml_id_start"],
                                        "xml_id_end": chunk["xml_id_end"],
                                    }
                                )
                            )
                elif row["abstract"] and row["abstract"].strip():
                    passages = [
                        ChatEvidencePassage(
                            evidence_id=f"{record.record_id}:abstract",
                            text=row["abstract"][:12_000],
                            section="abstract",
                        )
                    ]
                if passages:
                    result.append(
                        record.model_copy(
                            update={
                                "title": row["title"],
                                "authors": json.loads(row["authors"] or "[]"),
                                "doi": row["doi"],
                                "journal": row["journal"],
                                "publication_year": row["publication_year"],
                                "passages": passages,
                                "providers": [row["source"]] if article_id else record.providers,
                                "url": f"https://doi.org/{row['doi']}" if row["doi"] else None,
                            }
                        )
                    )
    by_id = {record.record_id: record for record in result}
    return [by_id[record.record_id] for record in records if record.record_id in by_id]
