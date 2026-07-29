"""Small explicit SQLite data-access layer with FTS5 enabled."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from app.database.migrations import ensure_current
from app.models.evidence import ArticleEvidence
from app.models.synthesis import FinalSynthesis, ThemePlan, ThemeSynthesis


def _cited_evidence_ids(document: ThemeSynthesis | FinalSynthesis) -> list[str]:
    fields = (("summary",) if isinstance(document, ThemeSynthesis) else ("direct_answer",)) + (
        "consensus",
        "convergent_results",
        "contradictory_results",
        "quantitative_results",
    )
    values: list[str] = []
    for field in fields:
        for statement in getattr(document, field, []):
            values.extend(statement.evidence_ids)
    return list(dict.fromkeys(values))


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        schema = schema_path.read_text(encoding="utf-8")
        with closing(self.connect()) as connection:
            connection.executescript(schema)
            ensure_current(connection)
            connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def purge_argo_request_events(self, *, before: datetime) -> int:
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("ARGO quota cutoff must be timezone-aware")
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM argo_request_events WHERE requested_at < ?",
                (before.isoformat(),),
            )
        return cursor.rowcount

    def create_chat_conversation(self, title: str = "Nouvelle conversation") -> dict[str, Any]:
        conversation_id = str(uuid.uuid4())
        cleaned_title = " ".join(title.split())[:120] or "Nouvelle conversation"
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO chat_conversations (id, title) VALUES (?, ?)",
                (conversation_id, cleaned_title),
            )
        conversation = self.chat_conversation(conversation_id)
        if conversation is None:
            raise RuntimeError("chat conversation was not persisted")
        return conversation

    def _conversation_summaries(
        self,
        connection: sqlite3.Connection,
        *,
        where_clause: str = "",
        parameters: Sequence[object] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return lightweight conversation cards, optionally restricted by a fixed predicate."""

        limit_clause = "LIMIT ?" if limit is not None else ""
        query_parameters = (*parameters, limit) if limit is not None else parameters
        rows = connection.execute(
            f"""
            SELECT conversation.id, conversation.title,
                   conversation.created_at, conversation.updated_at,
                   EXISTS(
                       SELECT 1 FROM chat_conversation_favorites AS favorite
                       WHERE favorite.conversation_id = conversation.id
                   ) AS favorite,
                   COUNT(message.id) AS message_count,
                   (
                       SELECT latest.content
                       FROM chat_messages AS latest
                       WHERE latest.conversation_id = conversation.id
                       ORDER BY latest.position DESC
                       LIMIT 1
                   ) AS last_message,
                   (
                       SELECT COUNT(*)
                       FROM jobs AS active_job
                       WHERE active_job.conversation_id = conversation.id
                         AND active_job.state IN (
                             'queued', 'running', 'cancel_requested'
                         )
                   ) AS active_job_count
            FROM chat_conversations AS conversation
            LEFT JOIN chat_messages AS message
                ON message.conversation_id = conversation.id
            {where_clause}
            GROUP BY conversation.id
            ORDER BY conversation.updated_at DESC, conversation.rowid DESC
            {limit_clause}
            """,
            query_parameters,
        ).fetchall()
        conversations = [dict(row) for row in rows]
        for conversation in conversations:
            conversation["favorite"] = bool(conversation["favorite"])
        return conversations

    def list_chat_conversations(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            return self._conversation_summaries(connection)

    def chat_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            conversation = connection.execute(
                """
                SELECT conversation.id, conversation.title,
                       conversation.created_at, conversation.updated_at,
                       EXISTS(
                           SELECT 1 FROM chat_conversation_favorites AS favorite
                           WHERE favorite.conversation_id = conversation.id
                       ) AS favorite,
                       COUNT(message.id) AS message_count,
                       (
                           SELECT latest.content
                           FROM chat_messages AS latest
                           WHERE latest.conversation_id = conversation.id
                           ORDER BY latest.position DESC
                           LIMIT 1
                       ) AS last_message
                FROM chat_conversations AS conversation
                LEFT JOIN chat_messages AS message
                    ON message.conversation_id = conversation.id
                WHERE conversation.id = ?
                GROUP BY conversation.id
                """,
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                return None
            message_rows = connection.execute(
                """
                SELECT message.id, message.role, message.content, message.response_json,
                       message.response_time_milliseconds, message.created_at,
                       feedback.helpful
                FROM chat_messages AS message
                LEFT JOIN chat_message_feedback AS feedback
                    ON feedback.message_id = message.id
                WHERE message.conversation_id = ?
                ORDER BY message.position
                """,
                (conversation_id,),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in message_rows:
            message = dict(row)
            serialized_response = message.pop("response_json")
            message["response"] = (
                json.loads(str(serialized_response)) if serialized_response is not None else None
            )
            message["helpful"] = (
                bool(message["helpful"]) if message["helpful"] is not None else None
            )
            messages.append(message)
        result = dict(conversation)
        result["favorite"] = bool(result["favorite"])
        return {**result, "messages": messages}

    def search_chat_conversations(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cleaned = " ".join(query.split())
        if len(cleaned) < 2:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("conversation search limit must be between 1 and 100")
        escaped = cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with closing(self.connect()) as connection:
            return self._conversation_summaries(
                connection,
                where_clause="""
                WHERE conversation.title LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR EXISTS(
                       SELECT 1 FROM chat_messages AS message
                       WHERE message.conversation_id = conversation.id
                         AND message.content LIKE ? ESCAPE '\\' COLLATE NOCASE
                   )
                """,
                parameters=(pattern, pattern),
                limit=limit,
            )

    def set_chat_conversation_favorite(self, conversation_id: str, favorite: bool) -> bool:
        with self.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM chat_conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if exists is None:
                return False
            if favorite:
                connection.execute(
                    """
                    INSERT INTO chat_conversation_favorites(conversation_id, created_at)
                    VALUES (?, CURRENT_TIMESTAMP)
                    ON CONFLICT(conversation_id) DO NOTHING
                    """,
                    (conversation_id,),
                )
            else:
                connection.execute(
                    "DELETE FROM chat_conversation_favorites WHERE conversation_id = ?",
                    (conversation_id,),
                )
        return True

    def set_chat_message_feedback(self, message_id: str, helpful: bool) -> bool:
        with self.transaction() as connection:
            message = connection.execute(
                "SELECT role FROM chat_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if message is None:
                return False
            if message["role"] != "assistant":
                raise ValueError("feedback is accepted only for assistant messages")
            connection.execute(
                """
                INSERT INTO chat_message_feedback(
                    message_id, helpful, created_at, updated_at
                ) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(message_id) DO UPDATE SET
                    helpful = excluded.helpful,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (message_id, int(helpful)),
            )
        return True

    def rename_chat_conversation(self, conversation_id: str, title: str) -> dict[str, Any] | None:
        cleaned_title = " ".join(title.split())[:120]
        if not cleaned_title:
            raise ValueError("chat conversation title cannot be empty")
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE chat_conversations
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cleaned_title, conversation_id),
            )
        return self.chat_conversation(conversation_id) if cursor.rowcount == 1 else None

    def delete_chat_conversation(self, conversation_id: str) -> bool:
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM chat_conversations WHERE id = ?", (conversation_id,)
            )
        return cursor.rowcount == 1

    def append_chat_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        response: dict[str, Any] | None = None,
        response_time_milliseconds: float | None = None,
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("chat message role is invalid")
        with self.transaction() as connection:
            conversation = connection.execute(
                "SELECT id FROM chat_conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ValueError("chat conversation does not exist")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM chat_messages WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO chat_messages (
                    id, conversation_id, position, role, content,
                    response_json, response_time_milliseconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    int(row[0]),
                    role,
                    content,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    response_time_milliseconds,
                ),
            )
            connection.execute(
                """
                UPDATE chat_conversations
                SET updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
                WHERE id = ?
                """,
                (conversation_id,),
            )

    def save_chat_turn(
        self,
        *,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        assistant_response: dict[str, Any],
        response_time_milliseconds: float,
    ) -> None:
        with self.transaction() as connection:
            conversation = connection.execute(
                "SELECT id FROM chat_conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ValueError("chat conversation does not exist")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM chat_messages WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            next_position = int(row[0])
            connection.executemany(
                """
                INSERT INTO chat_messages (
                    id, conversation_id, position, role, content,
                    response_json, response_time_milliseconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        conversation_id,
                        next_position,
                        "user",
                        user_content,
                        None,
                        None,
                    ),
                    (
                        str(uuid.uuid4()),
                        conversation_id,
                        next_position + 1,
                        "assistant",
                        assistant_content,
                        json.dumps(assistant_response, ensure_ascii=False),
                        response_time_milliseconds,
                    ),
                ],
            )
            connection.execute(
                "UPDATE chat_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (conversation_id,),
            )

    def article_by_sha256(self, sha256: str) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(
                "SELECT * FROM articles WHERE sha256 = ?", (sha256,)
            ).fetchone()

    def article_by_doi(self, doi: str) -> sqlite3.Row | None:
        """Return the existing local article for a normalized DOI, case-insensitively."""

        normalized = doi.strip().lower()
        if not normalized:
            return None
        with closing(self.connect()) as connection:
            return connection.execute(
                "SELECT * FROM articles WHERE doi = ? COLLATE NOCASE", (normalized,)
            ).fetchone()

    def article_with_first_chunk_by_doi(self, doi: str) -> sqlite3.Row | None:
        """Resolve local DOI metadata and, when present, one actually readable chunk."""

        normalized = doi.strip().lower()
        if not normalized:
            return None
        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT a.id AS article_id, a.doi, a.validation_status,
                       c.id AS chunk_id, c.page_start, c.page_end, c.text
                FROM articles AS a
                LEFT JOIN chunks AS c
                  ON c.id = (
                      SELECT selected.id
                      FROM chunks AS selected
                      WHERE selected.article_id = a.id
                      ORDER BY selected.chunk_index, selected.id
                      LIMIT 1
                  )
                WHERE a.doi = ? COLLATE NOCASE
                """,
                (normalized,),
            ).fetchone()

    def deep_research_citation_source(
        self,
        *,
        article_id: str,
        chunk_id: int,
    ) -> sqlite3.Row | None:
        """Return authoritative citation, bibliography, page and chunk text fields."""

        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT a.id AS article_id, a.title, a.authors, a.journal,
                       a.publication_year, a.doi, a.sha256 AS article_sha256,
                       c.id AS chunk_id, c.page_start, c.page_end, c.text
                FROM chunks AS c
                JOIN articles AS a ON a.id = c.article_id
                WHERE a.id = ? AND c.id = ?
                """,
                (article_id, chunk_id),
            ).fetchone()

    def chunk_count(self, article_id: str) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE article_id = ?", (article_id,)
            ).fetchone()
            return int(row[0])

    def save_article_and_chunks(
        self,
        article: dict[str, Any],
        chunks: Sequence[dict[str, Any]],
        document_elements: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        """Persist one article atomically; triggers populate FTS5."""

        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO articles (
                    id, sha256, doi, title, abstract, authors, journal,
                    publication_year, language, pdf_path, validation_status, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["id"],
                    article["sha256"],
                    article.get("doi"),
                    article["title"],
                    article.get("abstract"),
                    json.dumps(article.get("authors", []), ensure_ascii=False),
                    article.get("journal"),
                    article.get("publication_year"),
                    article.get("language"),
                    article["pdf_path"],
                    article.get("validation_status", "validated"),
                    article.get("source", "local"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO chunks (
                    article_id, section, subsection, page_start, page_end,
                    chunk_index, text, token_count, embedding_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        article["id"],
                        chunk.get("section"),
                        chunk.get("subsection"),
                        chunk["page_start"],
                        chunk["page_end"],
                        chunk["chunk_index"],
                        chunk["text"],
                        chunk["token_count"],
                        chunk.get("embedding_status", "pending"),
                    )
                    for chunk in chunks
                ],
            )
            persisted_chunks = connection.execute(
                """
                SELECT id, text
                FROM chunks
                WHERE article_id = ?
                ORDER BY chunk_index, id
                """,
                (article["id"],),
            ).fetchall()
            for element in document_elements or ():
                database_element_id = f"{article['id']}:{element['element_id']}"
                connection.execute(
                    """
                    INSERT INTO document_elements (
                        id, article_id, local_element_id, kind, page_number,
                        bbox_json, source_kind, source_locator,
                        original_caption, synthetic_caption
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        database_element_id,
                        article["id"],
                        element["element_id"],
                        element["kind"],
                        element["page_number"],
                        json.dumps(element["bbox"], separators=(",", ":")),
                        element["source_kind"],
                        element.get("source_locator"),
                        element.get("original_caption"),
                        element.get("synthetic_caption"),
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO document_table_cells (
                        element_id, row_index, column_index, text
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            database_element_id,
                            cell["row_index"],
                            cell["column_index"],
                            cell["text"],
                        )
                        for cell in element.get("cells", [])
                    ],
                )
                for relation in element.get("text_relations", []):
                    excerpt = str(relation["source_excerpt"])
                    related_chunk_id = next(
                        (int(row["id"]) for row in persisted_chunks if excerpt in str(row["text"])),
                        None,
                    )
                    connection.execute(
                        """
                        INSERT INTO document_element_relations (
                            element_id, relation, page_number, related_chunk_id,
                            source_excerpt, source_excerpt_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            database_element_id,
                            relation["relation"],
                            relation["page_number"],
                            related_chunk_id,
                            excerpt,
                            hashlib.sha256(excerpt.encode()).hexdigest(),
                        ),
                    )

    def document_element_count(self, article_id: str) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM document_elements WHERE article_id = ?",
                (article_id,),
            ).fetchone()
        return int(row[0])

    def document_elements(self, article_id: str) -> list[dict[str, Any]]:
        """Load source elements with cells and text relations kept structurally separate."""

        with closing(self.connect()) as connection:
            elements = connection.execute(
                """
                SELECT *
                FROM document_elements
                WHERE article_id = ?
                ORDER BY page_number, kind, local_element_id
                """,
                (article_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for element in elements:
                cells = connection.execute(
                    """
                    SELECT row_index, column_index, text
                    FROM document_table_cells
                    WHERE element_id = ?
                    ORDER BY row_index, column_index
                    """,
                    (element["id"],),
                ).fetchall()
                relations = connection.execute(
                    """
                    SELECT relation, page_number, related_chunk_id,
                           source_excerpt, source_excerpt_sha256
                    FROM document_element_relations
                    WHERE element_id = ?
                    ORDER BY relation, source_excerpt_sha256
                    """,
                    (element["id"],),
                ).fetchall()
                payload = dict(element)
                payload["bbox"] = json.loads(str(payload.pop("bbox_json")))
                payload["cells"] = [dict(row) for row in cells]
                payload["text_relations"] = [dict(row) for row in relations]
                result.append(payload)
        return result

    def set_synthetic_document_caption(
        self,
        element_id: str,
        caption: str,
    ) -> None:
        """Store generated retrieval text separately from immutable source captions."""

        cleaned = " ".join(caption.split())
        if not cleaned or len(cleaned) > 4000:
            raise ValueError("synthetic document caption is empty or too long")
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE document_elements
                SET synthetic_caption = ?
                WHERE id = ?
                """,
                (cleaned, element_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("document element does not exist")

    def save_ocr_page_traces(
        self,
        pdf_sha256: str,
        traces: Sequence[dict[str, Any]],
        *,
        article_id: str | None = None,
    ) -> None:
        """Persist every processed OCR page, including text rejected as evidence."""

        if len(pdf_sha256) != 64:
            raise ValueError("OCR trace PDF hash is invalid")
        with closing(self.connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO ocr_page_traces (
                    pdf_sha256, page_number, article_id, language, confidence,
                    confidence_method, embedded_text_original, ocr_text,
                    admitted, decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pdf_sha256, page_number) DO UPDATE SET
                    article_id = COALESCE(excluded.article_id, ocr_page_traces.article_id),
                    language = excluded.language,
                    confidence = excluded.confidence,
                    confidence_method = excluded.confidence_method,
                    embedded_text_original = excluded.embedded_text_original,
                    ocr_text = excluded.ocr_text,
                    admitted = excluded.admitted,
                    decision_reason = excluded.decision_reason
                """,
                [
                    (
                        pdf_sha256,
                        trace["page_number"],
                        article_id,
                        trace["language"],
                        trace["confidence"],
                        trace["confidence_method"],
                        trace["embedded_text_original"],
                        trace["ocr_text"],
                        int(bool(trace["admitted"])),
                        trace["decision_reason"],
                    )
                    for trace in traces
                ],
            )

    def ocr_page_traces(self, pdf_sha256: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM ocr_page_traces
                WHERE pdf_sha256 = ?
                ORDER BY page_number
                """,
                (pdf_sha256,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_ingestion_job(
        self,
        *,
        pdf_path: str,
        sha256: str,
        state: str,
        article_id: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        increment_attempt: bool = False,
    ) -> None:
        increment = 1 if increment_attempt else 0
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO ingestion_jobs (
                    pdf_path, sha256, state, article_id, error_type, error_message
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pdf_path, sha256) DO UPDATE SET
                    state = excluded.state,
                    article_id = COALESCE(excluded.article_id, ingestion_jobs.article_id),
                    error_type = excluded.error_type,
                    error_message = excluded.error_message,
                    attempt_count = ingestion_jobs.attempt_count + ?,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    pdf_path,
                    sha256,
                    state,
                    article_id,
                    error_type,
                    error_message,
                    increment,
                ),
            )

    def lexical_search(
        self,
        query: str,
        limit: int = 20,
        *,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
        section_weight: float = 1.5,
        text_weight: float = 1.0,
    ) -> list[sqlite3.Row]:
        """Execute one already-sanitized FTS5 expression with bounded SQL filters."""

        if not query.strip():
            return []
        if limit <= 0:
            raise ValueError("lexical search limit must be positive")
        if section_weight < 0 or text_weight < 0:
            raise ValueError("BM25 weights cannot be negative")
        if article_ids is not None and not article_ids:
            return []
        if sections is not None and not sections:
            return []

        predicates = [
            "chunks_fts MATCH ?",
            "a.validation_status IN ('validated', 'indexed')",
        ]
        parameters: list[Any] = [section_weight, text_weight, query]
        if article_ids is not None:
            placeholders = ",".join("?" for _ in article_ids)
            predicates.append(f"c.article_id IN ({placeholders})")
            parameters.extend(article_ids)
        if sections is not None:
            placeholders = ",".join("?" for _ in sections)
            predicates.append(f"c.section IN ({placeholders})")
            parameters.extend(sections)
        parameters.append(limit)
        sql = f"""
            SELECT
                c.*,
                a.title AS article_title,
                a.publication_year,
                bm25(chunks_fts, 0.0, 0.0, ?, ?) AS lexical_score
            FROM chunks_fts
            JOIN chunks AS c ON c.id = CAST(chunks_fts.chunk_id AS INTEGER)
            JOIN articles AS a ON a.id = c.article_id
            WHERE {" AND ".join(predicates)}
            ORDER BY lexical_score, c.id
            LIMIT ?
        """
        caption_predicates = [
            "document_element_captions_fts MATCH ?",
            "a.validation_status IN ('validated', 'indexed')",
            "r.related_chunk_id IS NOT NULL",
        ]
        caption_parameters: list[Any] = [query]
        if article_ids is not None:
            placeholders = ",".join("?" for _ in article_ids)
            caption_predicates.append(f"c.article_id IN ({placeholders})")
            caption_parameters.extend(article_ids)
        if sections is not None:
            placeholders = ",".join("?" for _ in sections)
            caption_predicates.append(f"c.section IN ({placeholders})")
            caption_parameters.extend(sections)
        caption_parameters.append(limit)
        caption_sql = f"""
            SELECT
                c.*,
                a.title AS article_title,
                a.publication_year,
                bm25(document_element_captions_fts) + 0.25 AS lexical_score
            FROM document_element_captions_fts
            JOIN document_elements AS d
              ON d.rowid = document_element_captions_fts.rowid
            JOIN document_element_relations AS r ON r.element_id = d.id
            JOIN chunks AS c ON c.id = r.related_chunk_id
            JOIN articles AS a ON a.id = c.article_id
            WHERE {" AND ".join(caption_predicates)}
            ORDER BY lexical_score, c.id
            LIMIT ?
        """
        with closing(self.connect()) as connection:
            rows = [
                *connection.execute(sql, parameters),
                *connection.execute(caption_sql, caption_parameters),
            ]
        best_by_chunk: dict[int, sqlite3.Row] = {}
        for row in rows:
            chunk_id = int(row["id"])
            existing = best_by_chunk.get(chunk_id)
            if existing is None or float(row["lexical_score"]) < float(existing["lexical_score"]):
                best_by_chunk[chunk_id] = row
        return sorted(
            best_by_chunk.values(),
            key=lambda row: (float(row["lexical_score"]), int(row["id"])),
        )[:limit]

    def chunks_for_embedding(
        self,
        *,
        after_id: int = 0,
        limit: int = 8,
        retry_failed: bool = False,
        article_ids: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        statuses = ("pending", "failed") if retry_failed else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        article_predicate = ""
        article_parameters: tuple[str, ...] = ()
        if article_ids is not None:
            unique_articles = tuple(dict.fromkeys(article_ids))
            if not unique_articles:
                return []
            article_placeholders = ",".join("?" for _ in unique_articles)
            article_predicate = f" AND article_id IN ({article_placeholders})"
            article_parameters = unique_articles
        sql = f"""
            SELECT id, article_id, section, page_start, page_end, text
            FROM chunks
            WHERE id > ? AND embedding_status IN ({placeholders})
              {article_predicate}
            ORDER BY id
            LIMIT ?
        """
        with closing(self.connect()) as connection:
            return list(connection.execute(sql, (after_id, *statuses, *article_parameters, limit)))

    def update_embedding_status(self, chunk_ids: Sequence[int], status: str) -> None:
        if not chunk_ids:
            return
        allowed = {"pending", "processing", "indexed", "failed"}
        if status not in allowed:
            raise ValueError(f"unsupported embedding status: {status}")
        placeholders = ",".join("?" for _ in chunk_ids)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE chunks SET embedding_status = ? WHERE id IN ({placeholders})",
                (status, *chunk_ids),
            )

    def embedding_status_counts(self) -> dict[str, int]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT embedding_status, COUNT(*) AS count FROM chunks GROUP BY embedding_status"
            )
            return {str(row["embedding_status"]): int(row["count"]) for row in rows}

    def reset_processing_embeddings(self, article_ids: Sequence[str] | None = None) -> int:
        """Recover work left in a transient state by an interrupted local process."""

        predicate = ""
        parameters: tuple[str, ...] = ()
        if article_ids is not None:
            unique_articles = tuple(dict.fromkeys(article_ids))
            if not unique_articles:
                return 0
            placeholders = ",".join("?" for _ in unique_articles)
            predicate = f" AND article_id IN ({placeholders})"
            parameters = unique_articles
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE chunks SET embedding_status = 'pending' "
                f"WHERE embedding_status = 'processing'{predicate}",
                parameters,
            )
            return int(cursor.rowcount)

    def list_articles(self, *, limit: int = 500) -> list[sqlite3.Row]:
        if not 1 <= limit <= 5000:
            raise ValueError("article list limit must be between 1 and 5000")
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        a.id, a.title, a.doi, a.journal, a.publication_year,
                        a.language, a.validation_status, a.pdf_path, a.source,
                        a.created_at, a.indexed_at,
                        COUNT(c.id) AS chunk_count,
                        SUM(CASE WHEN c.embedding_status = 'indexed' THEN 1 ELSE 0 END)
                            AS indexed_chunk_count
                    FROM articles AS a
                    LEFT JOIN chunks AS c ON c.article_id = a.id
                    GROUP BY a.id
                    ORDER BY a.created_at DESC, a.id
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def list_ingestion_jobs(
        self, *, states: Sequence[str] | None = None, limit: int = 200
    ) -> list[sqlite3.Row]:
        if not 1 <= limit <= 5000:
            raise ValueError("ingestion job list limit must be between 1 and 5000")
        predicate = ""
        parameters: list[Any] = []
        if states is not None:
            unique_states = list(dict.fromkeys(states))
            if not unique_states:
                return []
            placeholders = ",".join("?" for _ in unique_states)
            predicate = f"WHERE state IN ({placeholders})"
            parameters.extend(unique_states)
        parameters.append(limit)
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT id, pdf_path, sha256, state, article_id, error_type,
                           error_message, attempt_count, created_at, updated_at
                    FROM ingestion_jobs
                    {predicate}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                    """,
                    parameters,
                )
            )

    def article_chunk_ids(self, article_id: str) -> list[int]:
        with closing(self.connect()) as connection:
            return [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM chunks WHERE article_id = ? ORDER BY id",
                    (article_id,),
                )
            ]

    def reset_article_for_reindex(self, article_id: str) -> int:
        with self.transaction() as connection:
            if (
                connection.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone()
                is None
            ):
                raise ValueError("article is unavailable")
            cursor = connection.execute(
                "UPDATE chunks SET embedding_status = 'pending' WHERE article_id = ?",
                (article_id,),
            )
            connection.execute(
                """
                UPDATE articles
                SET validation_status = 'validated', indexed_at = NULL
                WHERE id = ?
                """,
                (article_id,),
            )
            return int(cursor.rowcount)

    def delete_article(self, article_id: str) -> int:
        """Delete metadata and dependent query history, but never the source PDF."""

        with self.transaction() as connection:
            if (
                connection.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone()
                is None
            ):
                return 0
            affected_queries: list[str] = []
            for row in connection.execute("SELECT id, selected_article_ids FROM queries"):
                try:
                    selected = json.loads(row["selected_article_ids"])
                except (TypeError, json.JSONDecodeError):
                    selected = []
                if article_id in selected:
                    affected_queries.append(str(row["id"]))
            if affected_queries:
                placeholders = ",".join("?" for _ in affected_queries)
                connection.execute(
                    f"DELETE FROM queries WHERE id IN ({placeholders})",
                    tuple(affected_queries),
                )
            connection.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            return len(affected_queries)

    def list_query_summaries(self, *, limit: int = 100) -> list[sqlite3.Row]:
        if not 1 <= limit <= 1000:
            raise ValueError("query list limit must be between 1 and 1000")
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        q.id, q.original_query, q.created_at, q.duration_seconds,
                        q.selected_article_ids, q.model_version,
                        SUM(CASE WHEN r.state = 'completed' THEN 1 ELSE 0 END)
                            AS evidence_completed,
                        SUM(CASE WHEN r.state = 'failed' THEN 1 ELSE 0 END)
                            AS evidence_failed,
                        COUNT(r.article_id) AS evidence_total,
                        s.state AS synthesis_state,
                        s.updated_at AS synthesis_updated_at
                    FROM queries AS q
                    LEFT JOIN article_evidence_runs AS r ON r.query_id = q.id
                    LEFT JOIN synthesis_runs AS s ON s.query_id = q.id
                    GROUP BY q.id
                    ORDER BY q.created_at DESC, q.id
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def evidence_run_rows_for_query(self, query_id: str) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT r.*, a.title
                    FROM article_evidence_runs AS r
                    JOIN articles AS a ON a.id = r.article_id
                    WHERE r.query_id = ?
                    ORDER BY r.created_at, r.article_id
                    """,
                    (query_id,),
                )
            )

    def chunks_by_ids(self, chunk_ids: Sequence[int]) -> dict[int, sqlite3.Row]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})", tuple(chunk_ids)
            )
            return {int(row["id"]): row for row in rows}

    def chunk_details_by_ids(self, chunk_ids: Sequence[int]) -> dict[int, sqlite3.Row]:
        """Hydrate retrieval candidates with authoritative article metadata."""

        if not chunk_ids:
            return {}
        placeholders = ",".join("?" for _ in chunk_ids)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    c.*,
                    a.title AS article_title,
                    a.publication_year,
                    a.language AS article_language
                FROM chunks AS c
                JOIN articles AS a ON a.id = c.article_id
                WHERE c.id IN ({placeholders})
                  AND a.validation_status IN ('validated', 'indexed')
                """,
                tuple(chunk_ids),
            )
            return {int(row["id"]): row for row in rows}

    def article_details_by_ids(self, article_ids: Sequence[str]) -> dict[str, sqlite3.Row]:
        """Return validated article metadata; SQLite remains the sole authority."""

        if not article_ids:
            return {}
        placeholders = ",".join("?" for _ in article_ids)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id, doi, title, abstract, authors, journal, publication_year,
                    language, pdf_path, validation_status, source, created_at, indexed_at
                FROM articles
                WHERE id IN ({placeholders})
                  AND validation_status IN ('validated', 'indexed')
                """,
                tuple(article_ids),
            )
            return {str(row["id"]): row for row in rows}

    def article_abstracts_by_title(
        self,
        title: str,
        *,
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        """Find validated abstracts when a question is an article title."""

        cleaned = " ".join(title.split())
        if not cleaned:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("article title match limit must be between 1 and 100")
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        id, doi, title, abstract, authors, journal, publication_year,
                        language, pdf_path, validation_status, source, created_at, indexed_at
                    FROM articles
                    WHERE validation_status IN ('validated', 'indexed')
                      AND abstract IS NOT NULL
                      AND trim(abstract) != ''
                      AND (
                          title = ? COLLATE NOCASE
                          OR instr(lower(title), lower(?)) > 0
                          OR (
                              length(title) >= 20
                              AND instr(lower(?), lower(title)) > 0
                          )
                      )
                    ORDER BY
                        CASE WHEN title = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                        publication_year DESC,
                        id
                    LIMIT ?
                    """,
                    (cleaned, cleaned, cleaned, cleaned, limit),
                )
            )

    def chunks_for_article(self, article_id: str, *, limit: int = 100) -> list[sqlite3.Row]:
        """Read a bounded candidate window from one validated article only."""

        if limit <= 0:
            raise ValueError("article chunk limit must be positive")
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT c.*
                    FROM chunks AS c
                    JOIN articles AS a ON a.id = c.article_id
                    WHERE c.article_id = ?
                      AND a.validation_status IN ('validated', 'indexed')
                    ORDER BY
                        CASE lower(COALESCE(c.section, ''))
                            WHEN 'results' THEN 0
                            WHEN 'discussion' THEN 1
                            WHEN 'conclusion' THEN 2
                            WHEN 'abstract' THEN 3
                            WHEN 'introduction' THEN 4
                            WHEN 'other' THEN 5
                            WHEN 'materials and methods' THEN 6
                            ELSE 5
                        END,
                        c.chunk_index
                    LIMIT ?
                    """,
                    (article_id, limit),
                )
            )

    def create_query(
        self,
        *,
        query_id: str,
        original_query: str,
        expanded_queries: Sequence[str],
        selected_article_ids: Sequence[str],
        corpus_version: str | None = None,
        model_version: str | None = None,
        parameters_hash: str | None = None,
    ) -> None:
        """Create one immutable query envelope used by resumable evidence runs."""

        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO queries (
                    id, original_query, expanded_queries, selected_article_ids,
                    corpus_version, model_version, parameters_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query_id,
                    original_query,
                    json.dumps(list(expanded_queries), ensure_ascii=False),
                    json.dumps(list(selected_article_ids), ensure_ascii=False),
                    corpus_version,
                    model_version,
                    parameters_hash,
                ),
            )

    def query_by_id(self, query_id: str) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()

    def start_article_evidence_run(
        self,
        *,
        query_id: str,
        article_id: str,
        selected_chunk_ids: Sequence[int],
    ) -> None:
        """Mark one article processing while retaining prior completed articles."""

        encoded_chunks = json.dumps(list(selected_chunk_ids))
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO article_evidence_runs (
                    query_id, article_id, state, selected_chunk_ids, attempt_count
                ) VALUES (?, ?, 'processing', ?, 1)
                ON CONFLICT(query_id, article_id) DO UPDATE SET
                    state = 'processing',
                    selected_chunk_ids = excluded.selected_chunk_ids,
                    attempt_count = article_evidence_runs.attempt_count + 1,
                    error_type = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (query_id, article_id, encoded_chunks),
            )

    def article_evidence_run(self, query_id: str, article_id: str) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT * FROM article_evidence_runs
                WHERE query_id = ? AND article_id = ?
                """,
                (query_id, article_id),
            ).fetchone()

    def save_article_evidence(
        self,
        *,
        query_id: str,
        evidence: ArticleEvidence,
        selected_chunk_ids: Sequence[int],
    ) -> None:
        """Atomically validate source identity, replace findings and complete the run."""

        allowed_ids = set(selected_chunk_ids)
        with self.transaction() as connection:
            rows = (
                connection.execute(
                    f"SELECT * FROM chunks WHERE id IN ({','.join('?' for _ in allowed_ids)})",
                    tuple(sorted(allowed_ids)),
                )
                if allowed_ids
                else []
            )
            chunks = {int(row["id"]): row for row in rows}
            if len(chunks) != len(allowed_ids):
                raise ValueError("selected evidence chunks are unavailable")
            if any(str(row["article_id"]) != evidence.article_id for row in chunks.values()):
                raise ValueError("selected evidence chunk belongs to another article")

            for finding in evidence.findings:
                chunk_id = int(finding.chunk_id)
                row = chunks.get(chunk_id)
                if row is None:
                    raise ValueError("finding references a non-selected chunk")
                if (
                    int(row["page_start"]) != finding.page_start
                    or int(row["page_end"]) != finding.page_end
                ):
                    raise ValueError("finding pages differ from SQLite")
                if finding.source_excerpt not in str(row["text"]):
                    raise ValueError("finding excerpt is not verbatim SQLite text")

            connection.execute(
                "DELETE FROM evidence WHERE query_id = ? AND article_id = ?",
                (query_id, evidence.article_id),
            )
            connection.executemany(
                """
                INSERT INTO evidence (
                    id, query_id, article_id, chunk_id, claim, source_excerpt,
                    page_start, page_end, relevance_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        query_id,
                        evidence.article_id,
                        int(finding.chunk_id),
                        finding.claim,
                        finding.source_excerpt,
                        finding.page_start,
                        finding.page_end,
                        evidence.relevance_score,
                    )
                    for finding in evidence.findings
                ],
            )
            cursor = connection.execute(
                """
                UPDATE article_evidence_runs
                SET state = 'completed',
                    relevance_score = ?,
                    question_addressed = ?,
                    topics = ?,
                    contradictions = ?,
                    missing_information = ?,
                    selected_chunk_ids = ?,
                    error_type = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ? AND article_id = ?
                """,
                (
                    evidence.relevance_score,
                    evidence.question_addressed,
                    json.dumps(evidence.topics, ensure_ascii=False),
                    json.dumps(evidence.contradictions, ensure_ascii=False),
                    json.dumps(evidence.missing_information, ensure_ascii=False),
                    json.dumps(list(selected_chunk_ids)),
                    query_id,
                    evidence.article_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("evidence run was not started")

    def fail_article_evidence_run(
        self,
        *,
        query_id: str,
        article_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE article_evidence_runs
                SET state = 'failed', error_type = ?, error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ? AND article_id = ?
                """,
                (error_type, error_message[:1000], query_id, article_id),
            )

    def load_article_evidence(self, query_id: str, article_id: str) -> ArticleEvidence | None:
        with closing(self.connect()) as connection:
            run = connection.execute(
                """
                SELECT * FROM article_evidence_runs
                WHERE query_id = ? AND article_id = ? AND state = 'completed'
                """,
                (query_id, article_id),
            ).fetchone()
            if run is None:
                return None
            findings = list(
                connection.execute(
                    """
                    SELECT claim, source_excerpt, page_start, page_end, chunk_id
                    FROM evidence
                    WHERE query_id = ? AND article_id = ?
                    ORDER BY rowid
                    """,
                    (query_id, article_id),
                )
            )
        return ArticleEvidence.model_validate(
            {
                "article_id": article_id,
                "relevance_score": run["relevance_score"],
                "question_addressed": run["question_addressed"],
                "findings": [
                    {
                        "claim": row["claim"],
                        "source_excerpt": row["source_excerpt"],
                        "page_start": row["page_start"],
                        "page_end": row["page_end"],
                        "chunk_id": str(row["chunk_id"]),
                    }
                    for row in findings
                ],
                "topics": json.loads(run["topics"]),
                "contradictions": json.loads(run["contradictions"]),
                "missing_information": json.loads(run["missing_information"]),
            }
        )

    def update_query_duration(self, query_id: str, duration_seconds: float) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE queries SET duration_seconds = ? WHERE id = ?",
                (duration_seconds, query_id),
            )

    def completed_article_evidence_rows(self, query_id: str) -> list[sqlite3.Row]:
        """Return completed article cards and SQLite metadata for one query."""

        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        r.query_id, r.article_id, r.relevance_score,
                        r.question_addressed, r.topics, r.contradictions,
                        r.missing_information, r.selected_chunk_ids,
                        a.title, a.authors, a.journal, a.publication_year,
                        a.doi, a.language
                    FROM article_evidence_runs AS r
                    JOIN articles AS a ON a.id = r.article_id
                    WHERE r.query_id = ? AND r.state = 'completed'
                    ORDER BY r.created_at, r.article_id
                    """,
                    (query_id,),
                )
            )

    def evidence_records_for_query(self, query_id: str) -> list[sqlite3.Row]:
        """Return stable evidence IDs joined to authoritative article metadata."""

        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        e.id AS evidence_id, e.query_id, e.article_id, e.chunk_id,
                        e.claim, e.source_excerpt, e.page_start, e.page_end,
                        e.relevance_score, a.title, a.authors, a.journal,
                        a.publication_year, a.doi, a.language
                    FROM evidence AS e
                    JOIN article_evidence_runs AS r
                      ON r.query_id = e.query_id AND r.article_id = e.article_id
                    JOIN articles AS a ON a.id = e.article_id
                    WHERE e.query_id = ? AND r.state = 'completed'
                    ORDER BY e.rowid
                    """,
                    (query_id,),
                )
            )

    def start_synthesis_run(
        self,
        *,
        query_id: str,
        model_version: str,
        reset: bool = False,
    ) -> None:
        """Start or resume a synthesis envelope while retaining completed themes."""

        with self.transaction() as connection:
            if reset:
                connection.execute("DELETE FROM synthesis_runs WHERE query_id = ?", (query_id,))
            connection.execute(
                """
                INSERT INTO synthesis_runs (
                    query_id, state, model_version, attempt_count
                ) VALUES (?, 'processing', ?, 1)
                ON CONFLICT(query_id) DO UPDATE SET
                    state = 'processing',
                    model_version = excluded.model_version,
                    attempt_count = synthesis_runs.attempt_count + 1,
                    error_type = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (query_id, model_version),
            )

    def synthesis_run(self, query_id: str) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(
                "SELECT * FROM synthesis_runs WHERE query_id = ?", (query_id,)
            ).fetchone()

    def save_theme_plan(self, query_id: str, plan: ThemePlan) -> None:
        """Persist an immutable plan and create resumable theme jobs."""

        theme_ids = [theme.theme_id for theme in plan.themes]
        if len(set(theme_ids)) != len(theme_ids):
            raise ValueError("theme plan contains duplicate theme identifiers")
        planned_articles = [article_id for theme in plan.themes for article_id in theme.article_ids]
        if len(set(planned_articles)) != len(planned_articles):
            raise ValueError("theme plan assigns an article more than once")
        with self.transaction() as connection:
            completed_articles = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT r.article_id
                    FROM article_evidence_runs AS r
                    JOIN evidence AS e
                      ON e.query_id = r.query_id AND e.article_id = r.article_id
                    WHERE r.query_id = ? AND r.state = 'completed'
                    """,
                    (query_id,),
                )
            }
            if set(planned_articles) != completed_articles:
                raise ValueError("theme plan must cover every completed article exactly once")
            cursor = connection.execute(
                """
                UPDATE synthesis_runs
                SET theme_plan = ?, updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ?
                """,
                (plan.model_dump_json(), query_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("synthesis run was not started")
            connection.executemany(
                """
                INSERT OR IGNORE INTO theme_synthesis_runs (
                    query_id, theme_id, state, theme_label, article_ids
                ) VALUES (?, ?, 'pending', ?, ?)
                """,
                [
                    (
                        query_id,
                        theme.theme_id,
                        theme.label,
                        json.dumps(theme.article_ids, ensure_ascii=False),
                    )
                    for theme in plan.themes
                ],
            )

    def load_theme_plan(self, query_id: str) -> ThemePlan | None:
        run = self.synthesis_run(query_id)
        if run is None or run["theme_plan"] is None:
            return None
        return ThemePlan.model_validate_json(str(run["theme_plan"]))

    def start_theme_synthesis(self, query_id: str, theme_id: str) -> None:
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE theme_synthesis_runs
                SET state = 'processing', attempt_count = attempt_count + 1,
                    error_type = NULL, error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ? AND theme_id = ?
                """,
                (query_id, theme_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("theme synthesis job is unavailable")

    def theme_synthesis_run(self, query_id: str, theme_id: str) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT * FROM theme_synthesis_runs
                WHERE query_id = ? AND theme_id = ?
                """,
                (query_id, theme_id),
            ).fetchone()

    def load_theme_synthesis(self, query_id: str, theme_id: str) -> ThemeSynthesis | None:
        row = self.theme_synthesis_run(query_id, theme_id)
        if row is None or row["state"] != "completed" or row["synthesis_json"] is None:
            return None
        return ThemeSynthesis.model_validate_json(str(row["synthesis_json"]))

    def save_theme_synthesis(self, *, query_id: str, synthesis: ThemeSynthesis) -> None:
        """Validate citations against the theme's persisted articles, then complete it."""

        with self.transaction() as connection:
            job = connection.execute(
                """
                SELECT * FROM theme_synthesis_runs
                WHERE query_id = ? AND theme_id = ?
                """,
                (query_id, synthesis.theme_id),
            ).fetchone()
            if job is None:
                raise ValueError("theme synthesis job was not started")
            expected_articles = json.loads(job["article_ids"])
            if synthesis.label != job["theme_label"]:
                raise ValueError("theme label differs from the persisted plan")
            if synthesis.article_ids != expected_articles:
                raise ValueError("theme articles differ from the persisted plan")
            evidence_rows = connection.execute(
                "SELECT id, article_id FROM evidence WHERE query_id = ?",
                (query_id,),
            )
            evidence_articles = {str(row["id"]): str(row["article_id"]) for row in evidence_rows}
            cited_ids = _cited_evidence_ids(synthesis)
            if any(
                evidence_id not in evidence_articles
                or evidence_articles[evidence_id] not in expected_articles
                for evidence_id in cited_ids
            ):
                raise ValueError("theme synthesis cites evidence outside its articles")
            connection.execute(
                """
                UPDATE theme_synthesis_runs
                SET state = 'completed', synthesis_json = ?, error_type = NULL,
                    error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ? AND theme_id = ?
                """,
                (synthesis.model_dump_json(), query_id, synthesis.theme_id),
            )

    def fail_theme_synthesis(
        self,
        *,
        query_id: str,
        theme_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE theme_synthesis_runs
                SET state = 'failed', error_type = ?, error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ? AND theme_id = ?
                """,
                (error_type, error_message[:1000], query_id, theme_id),
            )

    def save_final_synthesis(
        self,
        *,
        query_id: str,
        synthesis: FinalSynthesis,
        answer_markdown: str,
        cited_evidence_ids: Sequence[str] | None = None,
    ) -> None:
        """Atomically validate final evidence IDs and complete the synthesis."""

        cited_ids = list(dict.fromkeys(cited_evidence_ids or _cited_evidence_ids(synthesis)))
        if not set(_cited_evidence_ids(synthesis)).issubset(cited_ids):
            raise ValueError("persisted citation list omits final synthesis evidence")
        with self.transaction() as connection:
            allowed_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM evidence WHERE query_id = ?", (query_id,)
                )
            }
            if not set(cited_ids).issubset(allowed_ids):
                raise ValueError("final synthesis cites evidence outside its query")
            cursor = connection.execute(
                """
                UPDATE synthesis_runs
                SET state = 'completed', final_synthesis = ?, answer_markdown = ?,
                    cited_evidence_ids = ?, error_type = NULL, error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ?
                """,
                (
                    synthesis.model_dump_json(),
                    answer_markdown,
                    json.dumps(cited_ids),
                    query_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("synthesis run was not started")

    def load_final_synthesis(self, query_id: str) -> FinalSynthesis | None:
        run = self.synthesis_run(query_id)
        if run is None or run["state"] != "completed" or run["final_synthesis"] is None:
            return None
        return FinalSynthesis.model_validate_json(str(run["final_synthesis"]))

    def fail_synthesis_run(
        self,
        *,
        query_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE synthesis_runs
                SET state = 'failed', error_type = ?, error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE query_id = ?
                """,
                (error_type, error_message[:1000], query_id),
            )

    def refresh_fully_indexed_articles(self) -> int:
        """Promote only articles for which every fragment has a durable vector."""

        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE articles
                SET validation_status = 'indexed', indexed_at = CURRENT_TIMESTAMP
                WHERE validation_status IN ('validated', 'indexed')
                  AND EXISTS (
                      SELECT 1 FROM chunks WHERE chunks.article_id = articles.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM chunks
                      WHERE chunks.article_id = articles.id
                        AND chunks.embedding_status != 'indexed'
                  )
                """
            )
            return int(cursor.rowcount)

    def reset_all_embedding_statuses(self) -> int:
        """Prepare a reproducible vector rebuild while preserving article/chunk text."""

        with closing(self.connect()) as connection, connection:
            cursor = connection.execute("UPDATE chunks SET embedding_status = 'pending'")
            connection.execute(
                """
                UPDATE articles
                SET validation_status = 'validated', indexed_at = NULL
                WHERE validation_status = 'indexed'
                """
            )
            return int(cursor.rowcount)

    def publisher_records_for_targets(
        self, targets: Sequence[str]
    ) -> tuple[list[sqlite3.Row], list[str]]:
        """Resolve record IDs or normalized DOIs while preserving request order."""

        from app.updates.models import normalize_doi

        records: list[sqlite3.Row] = []
        missing: list[str] = []
        seen: set[str] = set()
        with closing(self.connect()) as connection:
            for target in targets:
                cleaned = target.strip()
                doi = normalize_doi(cleaned)
                row = connection.execute(
                    """
                    SELECT * FROM bibliographic_records
                    WHERE id = ? OR (? IS NOT NULL AND doi = ? COLLATE NOCASE)
                    LIMIT 1
                    """,
                    (cleaned, doi, doi),
                ).fetchone()
                if row is None:
                    missing.append(cleaned)
                    continue
                record_id = str(row["id"])
                if record_id not in seen:
                    records.append(row)
                    seen.add(record_id)
        return records, missing

    def create_publisher_access_run(
        self,
        *,
        profile_id: str,
        authorization_reference: str,
        record_ids: Sequence[str],
    ) -> str:
        run_id = str(uuid.uuid4())
        unique_record_ids = list(dict.fromkeys(record_ids))
        if not unique_record_ids:
            raise ValueError("publisher access run needs at least one record")
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO publisher_access_runs (
                    id, profile_id, authorization_reference, state,
                    requested_record_count
                ) VALUES (?, ?, ?, 'queued', ?)
                """,
                (run_id, profile_id, authorization_reference, len(unique_record_ids)),
            )
            connection.executemany(
                """
                INSERT INTO publisher_access_run_items (run_id, record_id, state)
                VALUES (?, ?, 'queued')
                """,
                [(run_id, record_id) for record_id in unique_record_ids],
            )
        return run_id

    def start_publisher_access_run(self, run_id: str) -> None:
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE publisher_access_runs
                SET state = 'running', started_at = CURRENT_TIMESTAMP,
                    error_type = NULL, error_message = NULL
                WHERE id = ? AND state = 'queued'
                """,
                (run_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("publisher access run is unavailable or already started")

    def mark_publisher_item_processing(self, run_id: str, record_id: str) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE publisher_access_run_items
                SET state = 'processing', error_type = NULL, error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND record_id = ?
                """,
                (run_id, record_id),
            )

    def save_publisher_asset(
        self,
        *,
        run_id: str,
        record_id: str,
        article_id: str | None,
        profile_id: str,
        acquisition_method: str,
        source_url: str,
        final_url: str,
        media_type: str,
        file_path: str,
        sha256: str,
        byte_count: int,
    ) -> str:
        asset_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO publisher_full_text_assets (
                    id, record_id, article_id, run_id, profile_id,
                    acquisition_method, source_url, final_url, media_type,
                    file_path, sha256, byte_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    record_id,
                    article_id,
                    run_id,
                    profile_id,
                    acquisition_method,
                    source_url,
                    final_url,
                    media_type,
                    file_path,
                    sha256,
                    byte_count,
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM publisher_full_text_assets
                WHERE record_id = ? AND sha256 = ?
                """,
                (record_id, sha256),
            ).fetchone()
            if row is None:
                raise RuntimeError("publisher asset was not persisted")
            persisted_asset_id = str(row["id"])
            connection.execute(
                """
                UPDATE publisher_access_run_items
                SET state = 'completed', asset_id = ?, error_type = NULL,
                    error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND record_id = ?
                """,
                (persisted_asset_id, run_id, record_id),
            )
        return persisted_asset_id

    def fail_publisher_access_item(
        self,
        *,
        run_id: str,
        record_id: str,
        error_type: str,
        error_message: str,
    ) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE publisher_access_run_items
                SET state = 'failed', error_type = ?, error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE run_id = ? AND record_id = ?
                """,
                (error_type[:200], error_message[:1000], run_id, record_id),
            )

    def complete_publisher_access_run(self, run_id: str) -> None:
        with self.transaction() as connection:
            counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT state, COUNT(*) AS count
                    FROM publisher_access_run_items
                    WHERE run_id = ? GROUP BY state
                    """,
                    (run_id,),
                )
            }
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            state = "completed" if failed == 0 else ("partial" if completed else "failed")
            connection.execute(
                """
                UPDATE publisher_access_runs
                SET state = ?, completed_record_count = ?, failed_record_count = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (state, completed, failed, run_id),
            )

    def fail_publisher_access_run(
        self, run_id: str, *, error_type: str, error_message: str
    ) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE publisher_access_runs
                SET state = 'failed', error_type = ?, error_message = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error_type[:200], error_message[:1000], run_id),
            )

    def publisher_access_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            run = connection.execute(
                "SELECT * FROM publisher_access_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            items = connection.execute(
                """
                SELECT record_id, state, asset_id, error_type, error_message, updated_at
                FROM publisher_access_run_items
                WHERE run_id = ? ORDER BY rowid
                """,
                (run_id,),
            ).fetchall()
        return {**dict(run), "items": [dict(item) for item in items]}
