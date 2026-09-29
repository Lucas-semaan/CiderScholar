"""Small explicit SQLite data-access layer with FTS5 enabled."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from app.database.migrations import ensure_current
from app.models.evidence import ArticleEvidence
from app.models.synthesis import FinalSynthesis, ThemePlan, ThemeSynthesis

_SHA256_HEX_LENGTH = 64
_EXTRACTION_RUN_STATES = frozenset({"started", "completed", "review_required", "failed"})
# A parser is a bounded local operation, not a multi-day background workflow.
_MAX_EXTRACTION_RUN_DURATION_SECONDS = 24 * 60 * 60


def _validate_extraction_run_hash(value: str, *, field_name: str) -> str:
    if len(value) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return value


def _validate_extraction_run_text(value: str, *, field_name: str, maximum: int) -> str:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{field_name} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} contains a control character")
    return value


def _validate_extraction_run_diagnostic(error_type: str, error_message: str) -> tuple[str, str]:
    return (
        _validate_extraction_run_text(error_type, field_name="error type", maximum=64),
        _validate_extraction_run_text(error_message, field_name="error message", maximum=240),
    )


def _native_outline_rows(
    nodes: Sequence[dict[str, Any]],
) -> list[tuple[str, str, str | None, int, str, str, int, str, str]]:
    """Validate parser-derived outline nodes before their native admission transaction."""

    local_ids = [str(node.get("node_id", "")) for node in nodes]
    if len(local_ids) != len(set(local_ids)) or any(not node_id for node_id in local_ids):
        raise ValueError("native outline node IDs must be non-empty and unique")
    known_ids = set(local_ids)
    previous_ordinal = -1
    rows: list[tuple[str, str, str | None, int, str, str, int, str, str]] = []
    for node in nodes:
        local_id = str(node["node_id"])
        parent = node.get("parent_node_id")
        if parent is not None and str(parent) not in known_ids:
            raise ValueError("native outline node parent is missing")
        ordinal = int(node["ordinal"])
        if ordinal <= previous_ordinal:
            raise ValueError("native outline node ordinals must be strictly increasing")
        previous_ordinal = ordinal
        parent_id = str(parent) if parent is not None else None
        level = int(node["level"])
        kind = str(node["kind"])
        title = str(node["title"])
        source_locator = str(node["source_locator"])
        payload = {
            "kind": kind,
            "level": level,
            "local_node_id": local_id,
            "ordinal": ordinal,
            "parent_local_node_id": parent_id,
            "source_locator": source_locator,
            "title": title,
        }
        structure_sha256 = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        rows.append(
            (
                local_id,
                local_id,
                parent_id,
                level,
                kind,
                title,
                ordinal,
                source_locator,
                structure_sha256,
            )
        )
    return rows


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


class DatabaseReadSession:
    """One explicitly closed, read-only SQLite connection for a retrieval batch.

    The session is deliberately short-lived (one user retrieval) so its page cache can
    accelerate query variants without becoming a second authority or retaining data.
    """

    def __init__(
        self,
        database: Database,
        *,
        cache_size_kib: int,
        mmap_size_bytes: int,
    ) -> None:
        self._database = database
        self._cache_size_kib = cache_size_kib
        self._mmap_size_bytes = mmap_size_bytes
        self._connection: sqlite3.Connection | None = None
        self._caption_index_state: tuple[int, bool] | None = None

    def __enter__(self) -> DatabaseReadSession:
        self._connection = self._database._connect_readonly(
            cache_size_kib=self._cache_size_kib,
            mmap_size_bytes=self._mmap_size_bytes,
        )
        return self

    def __exit__(self, *_args: object) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._caption_index_state = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("SQLite read session is not active")
        return self._connection

    def _caption_index_has_rows(self) -> bool:
        data_version = int(self.connection.execute("PRAGMA data_version").fetchone()[0])
        if self._caption_index_state is None or self._caption_index_state[0] != data_version:
            has_rows = self.connection.execute(
                "SELECT EXISTS(SELECT 1 FROM document_element_captions_fts LIMIT 1)"
            ).fetchone()[0]
            self._caption_index_state = (data_version, bool(has_rows))
        return self._caption_index_state[1]

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
        return self._database.lexical_search(
            query,
            limit,
            article_ids=article_ids,
            sections=sections,
            section_weight=section_weight,
            text_weight=text_weight,
            connection=self.connection,
            caption_index_has_rows=self._caption_index_has_rows(),
        )


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

    def _connect_readonly(
        self,
        *,
        cache_size_kib: int,
        mmap_size_bytes: int,
    ) -> sqlite3.Connection:
        if cache_size_kib <= 0 or mmap_size_bytes < 0:
            raise ValueError("SQLite read session limits must be non-negative")
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30.0
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute(f"PRAGMA cache_size = {-cache_size_kib}")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute(f"PRAGMA mmap_size = {mmap_size_bytes}")
        return connection

    def read_session(
        self,
        *,
        cache_size_kib: int = 32 * 1024,
        mmap_size_bytes: int = 128 * 1024 * 1024,
    ) -> DatabaseReadSession:
        """Create a bounded read-only session; callers must use it as a context manager."""

        return DatabaseReadSession(
            self,
            cache_size_kib=cache_size_kib,
            mmap_size_bytes=mmap_size_bytes,
        )

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

    def start_extraction_run(
        self,
        *,
        run_id: str,
        file_sha256: str,
        article_id: str | None,
        parser_id: str,
        parser_version: str,
        contract_version: str,
        config_sha256: str,
        model_name: str | None = None,
        model_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Persist or resume one parser identity without storing extracted text."""

        _validate_extraction_run_text(run_id, field_name="run ID", maximum=128)
        _validate_extraction_run_hash(file_sha256, field_name="file SHA-256")
        _validate_extraction_run_text(parser_id, field_name="parser ID", maximum=64)
        _validate_extraction_run_text(parser_version, field_name="parser version", maximum=128)
        _validate_extraction_run_text(
            contract_version,
            field_name="contract version",
            maximum=64,
        )
        _validate_extraction_run_hash(config_sha256, field_name="configuration SHA-256")
        if (model_name is None) != (model_sha256 is None):
            raise ValueError("model name and SHA-256 must be provided together")
        if model_name is not None and model_sha256 is not None:
            _validate_extraction_run_text(model_name, field_name="model name", maximum=256)
            _validate_extraction_run_hash(model_sha256, field_name="model SHA-256")

        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM extraction_runs
                WHERE file_sha256 = ?
                  AND parser_id = ?
                  AND parser_version = ?
                  AND contract_version = ?
                  AND config_sha256 = ?
                  AND model_name IS ?
                  AND model_sha256 IS ?
                """,
                (
                    file_sha256,
                    parser_id,
                    parser_version,
                    contract_version,
                    config_sha256,
                    model_name,
                    model_sha256,
                ),
            ).fetchone()
            if existing is not None:
                existing_article_id = existing["article_id"]
                if existing_article_id is None and article_id is not None:
                    connection.execute(
                        "UPDATE extraction_runs SET article_id = ?, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = ? AND article_id IS NULL",
                        (article_id, existing["id"]),
                    )
                    existing = connection.execute(
                        "SELECT * FROM extraction_runs WHERE id = ?", (existing["id"],)
                    ).fetchone()
                    if existing is None:  # pragma: no cover - row cannot disappear in transaction
                        raise RuntimeError("extraction run disappeared during article attachment")
                elif existing_article_id != article_id:
                    raise ValueError("extraction run identity belongs to another article")
                return dict(existing)
            existing_id = connection.execute(
                "SELECT 1 FROM extraction_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if existing_id is not None:
                raise ValueError("extraction run ID belongs to another identity")
            connection.execute(
                """
                INSERT INTO extraction_runs (
                    id, file_sha256, article_id, parser_id, parser_version,
                    contract_version, config_sha256, model_name, model_sha256, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'started')
                """,
                (
                    run_id,
                    file_sha256,
                    article_id,
                    parser_id,
                    parser_version,
                    contract_version,
                    config_sha256,
                    model_name,
                    model_sha256,
                ),
            )
            row = connection.execute(
                "SELECT * FROM extraction_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite INSERT contract
                raise RuntimeError("extraction run was not persisted")
            return dict(row)

    def complete_extraction_run(
        self,
        *,
        run_id: str,
        page_count: int,
        element_count: int,
        warning_count: int,
        normalized_text_sha256: str | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Atomically mark a started extraction as complete with bounded metadata."""

        self._finish_extraction_run(
            run_id=run_id,
            state="completed",
            page_count=page_count,
            element_count=element_count,
            warning_count=warning_count,
            normalized_text_sha256=normalized_text_sha256,
            duration_seconds=duration_seconds,
        )

    def mark_extraction_run_review_required(
        self,
        *,
        run_id: str,
        page_count: int,
        element_count: int,
        warning_count: int,
        normalized_text_sha256: str | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Atomically stop a started extraction pending explicit human review."""

        self._finish_extraction_run(
            run_id=run_id,
            state="review_required",
            page_count=page_count,
            element_count=element_count,
            warning_count=warning_count,
            normalized_text_sha256=normalized_text_sha256,
            duration_seconds=duration_seconds,
        )

    def fail_extraction_run(
        self,
        *,
        run_id: str,
        error_type: str,
        error_message: str,
        duration_seconds: float | None = None,
    ) -> None:
        """Atomically record a bounded technical failure for a started extraction."""

        error_type, error_message = _validate_extraction_run_diagnostic(
            error_type,
            error_message,
        )
        self._finish_extraction_run(
            run_id=run_id,
            state="failed",
            page_count=0,
            element_count=0,
            warning_count=0,
            duration_seconds=duration_seconds,
            error_type=error_type,
            error_message=error_message,
        )

    def extraction_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM extraction_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _finish_extraction_run(
        self,
        *,
        run_id: str,
        state: str,
        page_count: int,
        element_count: int,
        warning_count: int,
        normalized_text_sha256: str | None = None,
        duration_seconds: float | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Validate counters and provenance before committing a terminal extraction state."""

        if state not in _EXTRACTION_RUN_STATES - {"started"}:
            raise ValueError("extraction run terminal state is invalid")
        _validate_extraction_run_text(run_id, field_name="run ID", maximum=128)
        for name, value in (
            ("page count", page_count),
            ("element count", element_count),
            ("warning count", warning_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if normalized_text_sha256 is not None:
            _validate_extraction_run_hash(
                normalized_text_sha256,
                field_name="normalized text SHA-256",
            )
        if duration_seconds is not None and (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds)
            or not 0 <= duration_seconds <= _MAX_EXTRACTION_RUN_DURATION_SECONDS
        ):
            raise ValueError(
                "duration seconds must be finite and between 0 and "
                f"{_MAX_EXTRACTION_RUN_DURATION_SECONDS}"
            )
        if state == "failed":
            if error_type is None or error_message is None:
                raise ValueError("failed extraction run needs a technical diagnostic")
        elif error_type is not None or error_message is not None:
            raise ValueError("only failed extraction runs may store a diagnostic")

        with self.transaction() as connection:
            existing = connection.execute(
                """
                SELECT state, page_count, element_count, warning_count,
                       normalized_text_sha256, duration_seconds, error_type, error_message
                FROM extraction_runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if existing is None:
                raise ValueError("extraction run is unavailable")
            if existing["state"] != "started":
                expected = (
                    state,
                    page_count,
                    element_count,
                    warning_count,
                    normalized_text_sha256,
                    duration_seconds,
                    error_type,
                    error_message,
                )
                persisted = (
                    existing["state"],
                    existing["page_count"],
                    existing["element_count"],
                    existing["warning_count"],
                    existing["normalized_text_sha256"],
                    existing["duration_seconds"],
                    existing["error_type"],
                    existing["error_message"],
                )
                if persisted == expected:
                    return
                raise ValueError("extraction run is already terminal with a different payload")
            cursor = connection.execute(
                """
                UPDATE extraction_runs
                SET state = ?, page_count = ?, element_count = ?, warning_count = ?,
                    normalized_text_sha256 = ?, duration_seconds = ?,
                    error_type = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state = 'started'
                """,
                (
                    state,
                    page_count,
                    element_count,
                    warning_count,
                    normalized_text_sha256,
                    duration_seconds,
                    error_type,
                    error_message,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("started extraction run could not be finalized")

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

    def article_identity_candidates(self) -> list[sqlite3.Row]:
        """Return durable full-text identities for conservative content deduplication."""

        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT a.id, a.sha256, a.doi, a.title, a.publication_year, a.pdf_path
                    FROM articles AS a
                    WHERE EXISTS (
                        SELECT 1 FROM chunks AS c WHERE c.article_id = a.id
                    )
                    """
                )
            )

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
                  AND a.validation_status IN ('validated', 'indexed')
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
                  AND a.validation_status IN ('validated', 'indexed')
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
                    work_type, publisher, publication_year, language, pdf_path, validation_status,
                    source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["id"],
                    article["sha256"],
                    article.get("doi"),
                    article["title"],
                    article.get("abstract"),
                    json.dumps(article.get("authors", []), ensure_ascii=False),
                    article.get("journal"),
                    article.get("work_type"),
                    article.get("publisher"),
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

    def admit_native_asset_and_chunks(
        self,
        *,
        article: dict[str, Any],
        asset: dict[str, Any],
        chunks: Sequence[dict[str, Any]],
        outline_nodes: Sequence[dict[str, Any]] = (),
    ) -> tuple[str, list[int], bool]:
        """Atomically admit one verified native source and structural chunks.

        The native asset is idempotent per article/SHA.  A pre-existing PDF
        article remains the article authority and keeps its PDF asset primary;
        a native-only article deliberately has an empty legacy ``pdf_path`` so
        no consumer can mistake XML for a paginated PDF.
        """

        if not chunks:
            raise ValueError("native admission requires at least one chunk")
        doi = str(article["doi"]).strip().lower()
        if not doi:
            raise ValueError("native admission requires a normalized DOI")
        asset_sha256 = str(asset["sha256"])
        _validate_extraction_run_hash(asset_sha256, field_name="native asset SHA-256")
        if article.get("sha256") != asset_sha256:
            raise ValueError("native article and asset SHA-256 must match")
        asset_kind = str(asset["kind"])
        if asset_kind not in {
            "jats_xml",
            "tei_xml",
            "structured_xml",
            "cleaned_text",
            "plain_text",
        }:
            raise ValueError("native admission requires a native source asset")
        outline_rows = _native_outline_rows(outline_nodes)

        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM articles WHERE lower(doi) = ?", (doi,)
            ).fetchone()
            if existing is None:
                article_id = str(article["id"])
                connection.execute(
                    """
                    INSERT INTO articles (
                        id, sha256, doi, title, abstract, authors, journal,
                        work_type, publisher, publication_year, language, pdf_path,
                        validation_status, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'validated', ?)
                    """,
                    (
                        article_id,
                        asset_sha256,
                        doi,
                        article["title"],
                        article.get("abstract"),
                        json.dumps(article.get("authors", []), ensure_ascii=False),
                        article.get("journal"),
                        article.get("work_type"),
                        article.get("publisher"),
                        article.get("publication_year"),
                        article.get("language"),
                        article.get("source", "local"),
                    ),
                )
                has_pdf = False
            else:
                article_id = str(existing["id"])
                has_pdf = (
                    connection.execute(
                        "SELECT EXISTS(SELECT 1 FROM article_source_assets "
                        "WHERE article_id = ? AND kind = 'pdf' AND state = 'admitted')",
                        (article_id,),
                    ).fetchone()[0]
                    == 1
                )

            existing_asset = connection.execute(
                "SELECT id FROM article_source_assets WHERE article_id = ? AND sha256 = ?",
                (article_id, asset_sha256),
            ).fetchone()
            if existing_asset is not None:
                return article_id, [], True

            asset_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO article_source_assets(
                    id, article_id, kind, file_path, sha256, media_type, byte_count,
                    provider, source_url, license, state, is_primary, native_asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'admitted', ?, ?)
                """,
                (
                    asset_id,
                    article_id,
                    asset_kind,
                    asset["file_path"],
                    asset_sha256,
                    asset["media_type"],
                    asset["byte_count"],
                    asset.get("provider"),
                    asset.get("source_url"),
                    asset.get("license"),
                    0 if has_pdf else 1,
                    asset.get("native_asset_id"),
                ),
            )
            next_index = int(
                connection.execute(
                    "SELECT COALESCE(MAX(chunk_index), -1) + 1 FROM chunks WHERE article_id = ?",
                    (article_id,),
                ).fetchone()[0]
            )
            chunk_ids: list[int] = []
            for offset, chunk in enumerate(chunks):
                text = str(chunk["text"])
                cursor = connection.execute(
                    """
                    INSERT INTO chunks(
                        article_id, section, subsection, page_start, page_end,
                        chunk_index, text, token_count, embedding_status
                    ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, 'pending')
                    """,
                    (
                        article_id,
                        chunk.get("section"),
                        chunk.get("subsection"),
                        next_index + offset,
                        text,
                        chunk["token_count"],
                    ),
                )
                chunk_id = int(cursor.lastrowid)
                chunk_ids.append(chunk_id)
                connection.execute(
                    """
                    INSERT INTO chunk_locators(
                        chunk_id, asset_id, locator_kind, section_path, paragraph_start,
                        paragraph_end, xml_id_start, xml_id_end, span_sha256
                    ) VALUES (?, ?, 'structural', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        asset_id,
                        chunk["section_path"],
                        chunk["paragraph_start"],
                        chunk["paragraph_end"],
                        chunk.get("xml_id_start"),
                        chunk.get("xml_id_end"),
                        hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    ),
                )
            if outline_rows:
                durable_outline_rows = [
                    (
                        str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"ciderscholar:outline:{article_id}:{asset_id}:{local_node_id}",
                            )
                        ),
                        *row[1:],
                    )
                    for row in outline_rows
                    for local_node_id in (row[1],)
                ]
                connection.executemany(
                    """
                    INSERT INTO document_outline_nodes(
                        id, article_id, asset_id, extraction_run_id, local_node_id,
                        parent_local_node_id, level, kind, title, ordinal, source_locator,
                        structure_sha256
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            durable_id,
                            article_id,
                            asset_id,
                            local_node_id,
                            parent_local_node_id,
                            level,
                            kind,
                            title,
                            ordinal,
                            source_locator,
                            structure_sha256,
                        )
                        for (
                            durable_id,
                            local_node_id,
                            parent_local_node_id,
                            level,
                            kind,
                            title,
                            ordinal,
                            source_locator,
                            structure_sha256,
                        ) in durable_outline_rows
                    ],
                )
                node_by_locator = {row[7]: row[0] for row in durable_outline_rows}
                links = [
                    (chunk_id, node_by_locator.get(f"§ {str(chunk['section_path'])}"))
                    for chunk_id, chunk in zip(chunk_ids, chunks, strict=True)
                ]
                if any(node_id is None for _chunk_id, node_id in links):
                    raise ValueError("native chunk section has no matching outline node")
                connection.executemany(
                    "INSERT INTO chunk_outline_nodes(chunk_id, outline_node_id) VALUES (?, ?)",
                    links,
                )
        return article_id, chunk_ids, False

    def document_element_count(self, article_id: str) -> int:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM document_elements WHERE article_id = ?",
                (article_id,),
            ).fetchone()
        return int(row[0])

    def save_article_source_asset(
        self,
        *,
        article_id: str,
        kind: Literal["pdf", "jats_xml", "tei_xml", "structured_xml", "cleaned_text", "plain_text"],
        file_path: str,
        sha256: str,
        media_type: str,
        byte_count: int,
        provider: str | None = None,
        source_url: str | None = None,
        license: str | None = None,
        state: Literal["admitted", "superseded", "failed"] = "admitted",
        is_primary: bool = False,
        native_asset_id: str | None = None,
    ) -> str:
        """Persist one verified source asset without replacing the legacy PDF pointer.

        The caller owns path confinement and file hashing. This layer enforces only
        stable scalar bounds and makes selecting a new primary asset atomic.
        """

        _validate_extraction_run_text(article_id, field_name="article ID", maximum=128)
        _validate_extraction_run_text(file_path, field_name="asset file path", maximum=4_000)
        _validate_extraction_run_hash(sha256, field_name="asset SHA-256")
        _validate_extraction_run_text(media_type, field_name="asset media type", maximum=255)
        if byte_count <= 0:
            raise ValueError("asset byte count must be positive")
        for value, field_name, maximum in (
            (provider, "asset provider", 200),
            (source_url, "asset source URL", 2_000),
            (license, "asset license", 1_000),
            (native_asset_id, "native asset ID", 128),
        ):
            if value is not None:
                _validate_extraction_run_text(value, field_name=field_name, maximum=maximum)

        asset_id = str(uuid.uuid4())
        with self.transaction() as connection:
            if is_primary:
                connection.execute(
                    "UPDATE article_source_assets SET is_primary = 0, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE article_id = ? AND is_primary = 1",
                    (article_id,),
                )
            connection.execute(
                """
                INSERT INTO article_source_assets (
                    id, article_id, kind, file_path, sha256, media_type, byte_count,
                    provider, source_url, license, state, is_primary, native_asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id, sha256) DO UPDATE SET
                    kind = excluded.kind,
                    file_path = excluded.file_path,
                    media_type = excluded.media_type,
                    byte_count = excluded.byte_count,
                    provider = excluded.provider,
                    source_url = excluded.source_url,
                    license = excluded.license,
                    state = excluded.state,
                    is_primary = excluded.is_primary,
                    native_asset_id = excluded.native_asset_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    asset_id,
                    article_id,
                    kind,
                    file_path,
                    sha256,
                    media_type,
                    byte_count,
                    provider,
                    source_url,
                    license,
                    state,
                    int(is_primary),
                    native_asset_id,
                ),
            )
            row = connection.execute(
                "SELECT id FROM article_source_assets WHERE article_id = ? AND sha256 = ?",
                (article_id, sha256),
            ).fetchone()
        if row is None:  # pragma: no cover - SQLite INSERT contract
            raise RuntimeError("article source asset was not persisted")
        return str(row["id"])

    def article_source_assets(self, article_id: str) -> list[dict[str, Any]]:
        """Return source-asset provenance in preference order, without reading content."""

        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM article_source_assets
                WHERE article_id = ?
                ORDER BY is_primary DESC, created_at, id
                """,
                (article_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_document_outline(
        self,
        *,
        article_id: str,
        asset_id: str,
        nodes: Sequence[dict[str, Any]],
        extraction_run_id: str | None = None,
    ) -> dict[str, str]:
        """Persist one source-derived outline and return local-to-durable node IDs.

        Nodes are replaced only for the named asset, in one transaction.  This
        deliberately leaves outlines from other source assets untouched.
        """

        _validate_extraction_run_text(article_id, field_name="article ID", maximum=128)
        _validate_extraction_run_text(asset_id, field_name="asset ID", maximum=128)
        if extraction_run_id is not None:
            _validate_extraction_run_text(
                extraction_run_id, field_name="extraction run ID", maximum=128
            )
        local_ids = [str(node.get("node_id", "")) for node in nodes]
        if len(local_ids) != len(set(local_ids)) or any(not value for value in local_ids):
            raise ValueError("outline node IDs must be non-empty and unique")
        known_ids = set(local_ids)
        previous_ordinal = -1
        rows: list[tuple[Any, ...]] = []
        for node in nodes:
            local_id = str(node["node_id"])
            parent = node.get("parent_node_id")
            if parent is not None and str(parent) not in known_ids:
                raise ValueError("outline node parent is missing")
            ordinal = int(node["ordinal"])
            if ordinal <= previous_ordinal:
                raise ValueError("outline node ordinals must be strictly increasing")
            previous_ordinal = ordinal
            payload = {
                "kind": str(node["kind"]),
                "level": int(node["level"]),
                "local_node_id": local_id,
                "ordinal": ordinal,
                "parent_local_node_id": str(parent) if parent is not None else None,
                "source_locator": str(node["source_locator"]),
                "title": str(node["title"]),
            }
            structure_sha256 = hashlib.sha256(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            durable_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL, f"ciderscholar:outline:{article_id}:{asset_id}:{local_id}"
                )
            )
            rows.append(
                (
                    durable_id,
                    article_id,
                    asset_id,
                    extraction_run_id,
                    local_id,
                    payload["parent_local_node_id"],
                    payload["level"],
                    payload["kind"],
                    payload["title"],
                    ordinal,
                    payload["source_locator"],
                    structure_sha256,
                )
            )
        with self.transaction() as connection:
            asset = connection.execute(
                "SELECT article_id FROM article_source_assets WHERE id = ?", (asset_id,)
            ).fetchone()
            if asset is None or str(asset["article_id"]) != article_id:
                raise ValueError("outline asset must belong to the article")
            if extraction_run_id is not None:
                run = connection.execute(
                    "SELECT article_id FROM extraction_runs WHERE id = ?", (extraction_run_id,)
                ).fetchone()
                if run is None or (
                    run["article_id"] is not None and run["article_id"] != article_id
                ):
                    raise ValueError("outline extraction run must belong to the article")
            connection.execute(
                "DELETE FROM chunk_outline_nodes WHERE outline_node_id IN "
                "(SELECT id FROM document_outline_nodes WHERE article_id = ? AND asset_id = ?)",
                (article_id, asset_id),
            )
            connection.execute(
                "DELETE FROM document_outline_nodes WHERE article_id = ? AND asset_id = ?",
                (article_id, asset_id),
            )
            connection.executemany(
                """
                INSERT INTO document_outline_nodes(
                    id, article_id, asset_id, extraction_run_id, local_node_id,
                    parent_local_node_id, level, kind, title, ordinal, source_locator,
                    structure_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return {str(row[4]): str(row[0]) for row in rows}

    def save_chunk_outline_links(
        self,
        *,
        article_id: str,
        local_to_durable_node_ids: dict[str, str],
        chunk_to_local_node_ids: dict[int, str],
    ) -> None:
        """Link chunks to nodes of their own article, refusing cross-asset links."""

        _validate_extraction_run_text(article_id, field_name="article ID", maximum=128)
        if not chunk_to_local_node_ids:
            return
        rows = [
            (chunk_id, local_to_durable_node_ids.get(local_node_id))
            for chunk_id, local_node_id in chunk_to_local_node_ids.items()
        ]
        if any(chunk_id <= 0 or node_id is None for chunk_id, node_id in rows):
            raise ValueError("chunk outline links are invalid")
        with self.transaction() as connection:
            for chunk_id, node_id in rows:
                ownership = connection.execute(
                    """
                    SELECT c.article_id AS chunk_article_id, n.article_id AS node_article_id
                    FROM chunks AS c JOIN document_outline_nodes AS n ON n.id = ?
                    WHERE c.id = ?
                    """,
                    (node_id, chunk_id),
                ).fetchone()
                if (
                    ownership is None
                    or str(ownership["chunk_article_id"]) != article_id
                    or str(ownership["node_article_id"]) != article_id
                ):
                    raise ValueError("chunk outline node must belong to the same article")
            connection.executemany(
                """
                INSERT INTO chunk_outline_nodes(chunk_id, outline_node_id) VALUES (?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET outline_node_id = excluded.outline_node_id
                """,
                rows,
            )

    def document_outline(self, article_id: str) -> list[dict[str, Any]]:
        """Return persisted source-derived outline metadata without source contents."""

        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM document_outline_nodes
                WHERE article_id = ? ORDER BY asset_id, ordinal, id
                """,
                (article_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def rebuild_outline_retrieval_nodes(self, article_id: str) -> int:
        """Derive non-citable title/path navigation nodes from persisted source outline."""

        with self.transaction() as connection:
            rows = connection.execute(
                """SELECT id, asset_id, title, source_locator, structure_sha256
                   FROM document_outline_nodes WHERE article_id = ? ORDER BY asset_id, ordinal""",
                (article_id,),
            ).fetchall()
            connection.execute(
                "DELETE FROM outline_retrieval_nodes WHERE article_id = ?", (article_id,)
            )
            connection.executemany(
                """INSERT INTO outline_retrieval_nodes(
                    id, outline_node_id, article_id, asset_id, kind, text, source_sha256, citable
                ) VALUES (?, ?, ?, ?, 'title_path', ?, ?, 0)""",
                [
                    (
                        str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL, f"ciderscholar:outline-retrieval:{row['id']}"
                            )
                        ),
                        str(row["id"]),
                        article_id,
                        str(row["asset_id"]),
                        f"{row['title']}\n{row['source_locator']}",
                        str(row["structure_sha256"]),
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def chunks_for_outline_retrieval_node(
        self, retrieval_node_id: str, *, limit: int = 6
    ) -> list[sqlite3.Row]:
        """Expand a non-citable navigation hit into the article's citable chunks."""

        if not 1 <= limit <= 20:
            raise ValueError("outline expansion limit must be between 1 and 20")
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """SELECT chunk.* FROM outline_retrieval_nodes AS node
                       JOIN chunk_outline_nodes AS link
                         ON link.outline_node_id = node.outline_node_id
                       JOIN chunks AS chunk ON chunk.id = link.chunk_id
                       JOIN articles AS article ON article.id = chunk.article_id
                       WHERE node.id = ? AND article.validation_status IN ('validated', 'indexed')
                       ORDER BY chunk.chunk_index, chunk.id LIMIT ?""",
                    (retrieval_node_id, limit),
                )
            )

    def article_inspection(
        self,
        article_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any] | None:
        """Return a bounded, source-text-free inspection page for one article."""

        if not 1 <= limit <= 500:
            raise ValueError("inspection limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("inspection offset cannot be negative")
        with closing(self.connect()) as connection:
            article = connection.execute(
                "SELECT id FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            if article is None:
                return None
            assets = connection.execute(
                """
                SELECT id, kind, sha256, media_type, byte_count, provider, source_url,
                       license, state, is_primary, native_asset_id, created_at, updated_at
                FROM article_source_assets WHERE article_id = ?
                ORDER BY is_primary DESC, created_at, id LIMIT ? OFFSET ?
                """,
                (article_id, limit, offset),
            ).fetchall()
            outline = connection.execute(
                """
                SELECT id, asset_id, extraction_run_id, local_node_id, parent_local_node_id,
                       level, kind, title, ordinal, source_locator, structure_sha256
                FROM document_outline_nodes WHERE article_id = ?
                ORDER BY asset_id, ordinal, id LIMIT ? OFFSET ?
                """,
                (article_id, limit, offset),
            ).fetchall()
            elements = connection.execute(
                """
                SELECT id, local_element_id, kind, page_number, bbox_json, source_kind,
                       source_locator, length(original_caption) AS original_caption_length,
                       synthetic_caption IS NOT NULL AS has_synthetic_caption
                FROM document_elements WHERE article_id = ?
                ORDER BY page_number, id LIMIT ? OFFSET ?
                """,
                (article_id, limit, offset),
            ).fetchall()
            chunks = connection.execute(
                """
                SELECT c.id, c.section, c.subsection, c.page_start, c.page_end,
                       c.chunk_index, c.token_count, c.embedding_status,
                       l.asset_id, l.locator_kind, l.section_path, l.paragraph_start,
                       l.paragraph_end, l.xml_id_start, l.xml_id_end, l.span_sha256,
                       o.outline_node_id
                FROM chunks AS c
                LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                LEFT JOIN chunk_outline_nodes AS o ON o.chunk_id = c.id
                WHERE c.article_id = ? ORDER BY c.chunk_index, c.id LIMIT ? OFFSET ?
                """,
                (article_id, limit, offset),
            ).fetchall()
            runs = connection.execute(
                """
                SELECT id, file_sha256, parser_id, parser_version, contract_version,
                       config_sha256, model_name, model_sha256, state, page_count,
                       element_count, warning_count, normalized_text_sha256,
                       duration_seconds, started_at, updated_at, completed_at
                FROM extraction_runs WHERE article_id = ?
                ORDER BY started_at DESC, id LIMIT ? OFFSET ?
                """,
                (article_id, limit, offset),
            ).fetchall()
            totals = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM article_source_assets WHERE article_id = ?) AS assets,
                    (SELECT COUNT(*) FROM document_outline_nodes WHERE article_id = ?) AS outline,
                    (SELECT COUNT(*) FROM document_elements WHERE article_id = ?) AS elements,
                    (SELECT COUNT(*) FROM chunks WHERE article_id = ?) AS chunks,
                    (SELECT COUNT(*) FROM extraction_runs WHERE article_id = ?) AS extraction_runs
                """,
                (article_id, article_id, article_id, article_id, article_id),
            ).fetchone()
        return {
            "article_id": article_id,
            "limit": limit,
            "offset": offset,
            "totals": dict(totals),
            "assets": [dict(row) for row in assets],
            "outline": [dict(row) for row in outline],
            "elements": [dict(row) for row in elements],
            "chunks": [dict(row) for row in chunks],
            "extraction_runs": [dict(row) for row in runs],
        }

    def propose_chunk_correction(
        self, *, chunk_id: int, corrected_text: str, reason: str, reviewer: str
    ) -> str:
        """Store a curator proposal while retaining the immutable source chunk."""

        if chunk_id <= 0:
            raise ValueError("chunk correction needs a positive chunk ID")
        for value, name, maximum in (
            (corrected_text, "corrected text", 100_000),
            (reason, "reason", 1_000),
            (reviewer, "reviewer", 200),
        ):
            _validate_extraction_run_text(value, field_name=name, maximum=maximum)
        with self.transaction() as connection:
            chunk = connection.execute(
                "SELECT text FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
            if chunk is None:
                raise ValueError("chunk correction target is unavailable")
            original_hash = hashlib.sha256(str(chunk["text"]).encode("utf-8")).hexdigest()
            correction_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO chunk_corrections(
                    id, chunk_id, original_text_sha256, corrected_text, reason, reviewer, state
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed')
                ON CONFLICT(chunk_id, original_text_sha256, corrected_text) DO UPDATE SET
                    reason = excluded.reason, reviewer = excluded.reviewer
                """,
                (correction_id, chunk_id, original_hash, corrected_text, reason, reviewer),
            )
            row = connection.execute(
                """SELECT id FROM chunk_corrections WHERE chunk_id = ?
                   AND original_text_sha256 = ? AND corrected_text = ?""",
                (chunk_id, original_hash, corrected_text),
            ).fetchone()
        return str(row["id"])

    def decide_chunk_correction(self, correction_id: str, *, approved: bool) -> None:
        """Approve one current proposal and invalidate only its derived embedding."""

        with self.transaction() as connection:
            correction = connection.execute(
                "SELECT chunk_id, state FROM chunk_corrections WHERE id = ?", (correction_id,)
            ).fetchone()
            if correction is None or correction["state"] != "proposed":
                raise ValueError("chunk correction is not awaiting review")
            if approved:
                connection.execute(
                    """UPDATE chunk_corrections
                       SET state = 'superseded', reviewed_at = CURRENT_TIMESTAMP
                       WHERE chunk_id = ? AND state = 'approved'""",
                    (correction["chunk_id"],),
                )
                connection.execute(
                    """UPDATE chunk_corrections
                       SET state = 'approved', reviewed_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (correction_id,),
                )

                connection.execute(
                    "UPDATE chunks SET embedding_status = 'pending' WHERE id = ?",
                    (correction["chunk_id"],),
                )
                connection.execute(
                    "DELETE FROM chunks_fts WHERE rowid = ?",
                    (correction["chunk_id"],),
                )
                connection.execute(
                    """INSERT INTO chunks_fts(rowid, chunk_id, article_id, section, text)
                       SELECT c.id, CAST(c.id AS TEXT), c.article_id, c.section,
                              correction.corrected_text
                       FROM chunks AS c JOIN chunk_corrections AS correction
                         ON correction.id = ?
                       WHERE c.id = correction.chunk_id""",
                    (correction_id,),
                )
            else:
                connection.execute(
                    """UPDATE chunk_corrections
                       SET state = 'rejected', reviewed_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (correction_id,),
                )

    def chunk_corrections(self, article_id: str) -> list[dict[str, Any]]:
        """Return the auditable correction history without exposing original chunk text."""

        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT correction.id, correction.chunk_id, correction.original_text_sha256,
                          correction.corrected_text, correction.reason, correction.reviewer,
                          correction.state, correction.created_at, correction.reviewed_at
                   FROM chunk_corrections AS correction JOIN chunks AS chunk
                     ON chunk.id = correction.chunk_id
                   WHERE chunk.article_id = ? ORDER BY correction.created_at DESC, correction.id""",
                (article_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def outline_expanded_chunk_ids(
        self,
        seed_chunk_ids: Sequence[int],
        *,
        candidate_limit: int,
        passages_per_article: int,
    ) -> list[int]:
        """Return bounded sibling chunks sharing an authoritative outline node."""

        if not seed_chunk_ids or candidate_limit <= 0 or passages_per_article <= 0:
            return []
        placeholders = ",".join("?" for _ in seed_chunk_ids)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT sibling.id, sibling.article_id, sibling.chunk_index,
                       seed.chunk_id AS seed_chunk_id
                FROM chunk_outline_nodes AS seed
                JOIN chunk_outline_nodes AS sibling_link
                  ON sibling_link.outline_node_id = seed.outline_node_id
                JOIN chunks AS sibling ON sibling.id = sibling_link.chunk_id
                JOIN articles AS article ON article.id = sibling.article_id
                WHERE seed.chunk_id IN ({placeholders})
                  AND article.validation_status IN ('validated', 'indexed')
                ORDER BY sibling.article_id, sibling.chunk_index, sibling.id
                """,
                tuple(seed_chunk_ids),
            ).fetchall()
        seed_order = {chunk_id: position for position, chunk_id in enumerate(seed_chunk_ids)}
        ordered = sorted(
            rows,
            key=lambda row: (
                seed_order.get(int(row["seed_chunk_id"]), len(seed_order)),
                str(row["article_id"]),
                int(row["chunk_index"]),
                int(row["id"]),
            ),
        )
        selected: list[int] = []
        per_article: dict[str, int] = {}
        seen: set[int] = set(seed_chunk_ids)
        for row in ordered:
            chunk_id = int(row["id"])
            article_id = str(row["article_id"])
            if chunk_id in seen or per_article.get(article_id, 0) >= passages_per_article:
                continue
            selected.append(chunk_id)
            seen.add(chunk_id)
            per_article[article_id] = per_article.get(article_id, 0) + 1
            if len(selected) >= candidate_limit:
                break
        return selected

    def save_page_chunk_locators(self, *, article_id: str, asset_id: str) -> None:
        """Attach deterministic page locators to every persisted PDF chunk of one article."""

        _validate_extraction_run_text(article_id, field_name="article ID", maximum=128)
        _validate_extraction_run_text(asset_id, field_name="asset ID", maximum=128)
        with self.transaction() as connection:
            asset = connection.execute(
                "SELECT article_id, kind FROM article_source_assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
            if asset is None or str(asset["article_id"]) != article_id or asset["kind"] != "pdf":
                raise ValueError("page locators require a PDF asset from the same article")
            rows = connection.execute(
                "SELECT id, page_start, page_end, text FROM chunks WHERE article_id = ?",
                (article_id,),
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO chunk_locators(
                    chunk_id, asset_id, locator_kind, page_start, page_end, span_sha256
                ) VALUES (?, ?, 'page', ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    locator_kind = excluded.locator_kind,
                    page_start = excluded.page_start,
                    page_end = excluded.page_end,
                    section_path = NULL,
                    paragraph_start = NULL,
                    paragraph_end = NULL,
                    xml_id_start = NULL,
                    xml_id_end = NULL,
                    span_sha256 = excluded.span_sha256
                """,
                [
                    (
                        int(row["id"]),
                        asset_id,
                        int(row["page_start"]),
                        int(row["page_end"]),
                        hashlib.sha256(str(row["text"]).encode("utf-8")).hexdigest(),
                    )
                    for row in rows
                ],
            )

    def chunk_locator(self, chunk_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM chunk_locators WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def native_source_view(self, article_id: str) -> dict[str, list[dict[str, Any]]] | None:
        """Expose approved native source passages as data, never executable XML/HTML."""

        with closing(self.connect()) as connection:
            article = connection.execute(
                "SELECT 1 FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            if article is None:
                return None
            assets = connection.execute(
                """
                SELECT id, kind, sha256, media_type, byte_count, provider, source_url, license
                FROM article_source_assets
                WHERE article_id = ? AND kind IN ('jats_xml', 'tei_xml') AND state = 'admitted'
                ORDER BY created_at, id
                """,
                (article_id,),
            ).fetchall()
            passages = connection.execute(
                """
                SELECT c.id AS chunk_id, c.section, c.chunk_index,
                       COALESCE(correction.corrected_text, c.text) AS text,
                       l.asset_id, l.section_path, l.paragraph_start, l.paragraph_end,
                       l.xml_id_start, l.xml_id_end, l.span_sha256
                FROM chunks AS c
                JOIN chunk_locators AS l ON l.chunk_id = c.id
                JOIN article_source_assets AS asset ON asset.id = l.asset_id
                LEFT JOIN chunk_corrections AS correction
                  ON correction.chunk_id = c.id AND correction.state = 'approved'
                WHERE c.article_id = ?
                  AND asset.kind IN ('jats_xml', 'tei_xml')
                  AND asset.state = 'admitted'
                  AND l.locator_kind = 'structural'
                ORDER BY l.asset_id, c.chunk_index, c.id
                """,
                (article_id,),
            ).fetchall()
        return {
            "assets": [dict(asset) for asset in assets],
            "passages": [dict(passage) for passage in passages],
        }

    def save_structural_chunk_locator(
        self,
        *,
        chunk_id: int,
        asset_id: str,
        section_path: str,
        paragraph_start: int,
        paragraph_end: int,
        span_text: str,
        xml_id_start: str | None = None,
        xml_id_end: str | None = None,
    ) -> None:
        """Persist a source-native XML/text locator while forbidding fake pages."""

        if chunk_id <= 0 or paragraph_start < 0 or paragraph_end < paragraph_start:
            raise ValueError("structural locator bounds are invalid")
        _validate_extraction_run_text(asset_id, field_name="asset ID", maximum=128)
        _validate_extraction_run_text(section_path, field_name="section path", maximum=2_000)
        if not span_text:
            raise ValueError("structural locator span cannot be empty")
        for value, field_name in ((xml_id_start, "start XML ID"), (xml_id_end, "end XML ID")):
            if value is not None:
                _validate_extraction_run_text(value, field_name=field_name, maximum=255)
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT c.article_id, a.article_id AS asset_article_id, a.kind
                FROM chunks AS c
                JOIN article_source_assets AS a ON a.id = ?
                WHERE c.id = ?
                """,
                (asset_id, chunk_id),
            ).fetchone()
            if row is None or row["article_id"] != row["asset_article_id"]:
                raise ValueError("structural locator asset must belong to the chunk article")
            if row["kind"] == "pdf":
                raise ValueError("structural locator cannot target a PDF asset")
            connection.execute(
                """
                INSERT INTO chunk_locators(
                    chunk_id, asset_id, locator_kind, section_path, paragraph_start,
                    paragraph_end, xml_id_start, xml_id_end, span_sha256
                ) VALUES (?, ?, 'structural', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    locator_kind = excluded.locator_kind,
                    page_start = NULL,
                    page_end = NULL,
                    section_path = excluded.section_path,
                    paragraph_start = excluded.paragraph_start,
                    paragraph_end = excluded.paragraph_end,
                    xml_id_start = excluded.xml_id_start,
                    xml_id_end = excluded.xml_id_end,
                    span_sha256 = excluded.span_sha256
                """,
                (
                    chunk_id,
                    asset_id,
                    section_path,
                    paragraph_start,
                    paragraph_end,
                    xml_id_start,
                    xml_id_end,
                    hashlib.sha256(span_text.encode("utf-8")).hexdigest(),
                ),
            )

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

    def table_evidence(self, article_id: str) -> list[object]:
        """Project persisted source tables into deterministic evidence objects."""

        from app.models.table_evidence import table_evidence_from_element

        return [
            table_evidence_from_element(element)
            for element in self.document_elements(article_id)
            if element["kind"] == "table"
        ]

    def figure_evidence(self, article_id: str) -> list[object]:
        """Project only citable source captions and surrounding source text links."""

        from app.models.figure_evidence import figure_evidence_from_element

        return [
            figure_evidence_from_element(element)
            for element in self.document_elements(article_id)
            if element["kind"] == "figure"
        ]

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

    def figure_analysis(
        self,
        *,
        element_id: str,
        analysis_contract_sha256: str,
        image_sha256: str,
        model_name: str,
        model_revision: str,
    ) -> dict[str, Any] | None:
        """Load one content-addressed local visual analysis."""

        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM figure_analysis_runs
                WHERE element_id = ?
                  AND analysis_contract_sha256 = ?
                  AND image_sha256 = ?
                  AND model_name = ?
                  AND model_revision = ?
                """,
                (
                    element_id,
                    analysis_contract_sha256,
                    image_sha256,
                    model_name,
                    model_revision,
                ),
            ).fetchone()
        return self._figure_analysis_payload(row) if row is not None else None

    def save_figure_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        """Persist structured visual evidence without storing the rendered image."""

        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO figure_analysis_runs (
                    id, element_id, source_document_sha256, question_sha256,
                    image_sha256, analysis_contract_sha256, model_name,
                    model_revision, prompt_version,
                    figure_type, relevance_score, readability_score,
                    supports_answer, status, validation_reason, observation_text,
                    visible_variables_json, visible_units_json, trends_json,
                    limitations_json, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    element_id, analysis_contract_sha256, image_sha256,
                    model_name, model_revision
                )
                DO UPDATE SET
                    figure_type = excluded.figure_type,
                    relevance_score = excluded.relevance_score,
                    readability_score = excluded.readability_score,
                    supports_answer = excluded.supports_answer,
                    status = excluded.status,
                    validation_reason = excluded.validation_reason,
                    observation_text = excluded.observation_text,
                    visible_variables_json = excluded.visible_variables_json,
                    visible_units_json = excluded.visible_units_json,
                    trends_json = excluded.trends_json,
                    limitations_json = excluded.limitations_json,
                    duration_seconds = excluded.duration_seconds,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    analysis["id"],
                    analysis["element_id"],
                    analysis["source_document_sha256"],
                    analysis["question_sha256"],
                    analysis["image_sha256"],
                    analysis["analysis_contract_sha256"],
                    analysis["model_name"],
                    analysis["model_revision"],
                    analysis["prompt_version"],
                    analysis["figure_type"],
                    analysis["relevance_score"],
                    analysis["readability_score"],
                    int(bool(analysis["supports_answer"])),
                    analysis["status"],
                    analysis["validation_reason"],
                    analysis["observation_text"],
                    json.dumps(analysis.get("visible_variables", []), ensure_ascii=False),
                    json.dumps(analysis.get("visible_units", []), ensure_ascii=False),
                    json.dumps(analysis.get("trends", []), ensure_ascii=False),
                    json.dumps(analysis.get("limitations", []), ensure_ascii=False),
                    analysis["duration_seconds"],
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM figure_analysis_runs
                WHERE element_id = ?
                  AND analysis_contract_sha256 = ?
                  AND image_sha256 = ?
                  AND model_name = ?
                  AND model_revision = ?
                """,
                (
                    analysis["element_id"],
                    analysis["analysis_contract_sha256"],
                    analysis["image_sha256"],
                    analysis["model_name"],
                    analysis["model_revision"],
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("figure analysis was not persisted")
        return self._figure_analysis_payload(row)

    def figure_analysis_citation_source(self, analysis_id: str) -> sqlite3.Row | None:
        """Return the authoritative article and admitted visual observation."""

        with closing(self.connect()) as connection:
            return connection.execute(
                """
                SELECT
                    f.id AS figure_analysis_id,
                    f.observation_text,
                    f.image_sha256,
                    f.model_name,
                    f.model_revision,
                    f.prompt_version,
                    f.analysis_contract_sha256,
                    f.validation_reason,
                    f.relevance_score,
                    f.readability_score,
                    d.id AS element_id,
                    d.local_element_id,
                    d.page_number,
                    d.original_caption,
                    a.id AS article_id,
                    a.sha256 AS article_sha256,
                    a.title,
                    a.authors,
                    a.journal,
                    a.publication_year,
                    a.doi
                FROM figure_analysis_runs AS f
                JOIN document_elements AS d ON d.id = f.element_id
                JOIN articles AS a ON a.id = d.article_id
                WHERE f.id = ? AND f.status = 'validated'
                """,
                (analysis_id,),
            ).fetchone()

    @staticmethod
    def _figure_analysis_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        for stored, public in (
            ("visible_variables_json", "visible_variables"),
            ("visible_units_json", "visible_units"),
            ("trends_json", "trends"),
            ("limitations_json", "limitations"),
        ):
            value = json.loads(str(payload.pop(stored)))
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise RuntimeError("stored figure analysis list is invalid")
            payload[public] = value
        payload["supports_answer"] = bool(payload["supports_answer"])
        payload["admitted"] = payload["status"] == "validated"
        return payload

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
        connection: sqlite3.Connection | None = None,
        caption_index_has_rows: bool | None = None,
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
                COALESCE(correction.corrected_text, c.text) AS effective_text,
                l.locator_kind,
                CASE WHEN l.locator_kind = 'structural' THEN NULL
                     ELSE COALESCE(l.page_start, c.page_start) END AS locator_page_start,
                CASE WHEN l.locator_kind = 'structural' THEN NULL
                     ELSE COALESCE(l.page_end, c.page_end) END AS locator_page_end,
                l.section_path, l.paragraph_start, l.paragraph_end,
                l.xml_id_start, l.xml_id_end,
                a.title AS article_title,
                a.publication_year,
                bm25(chunks_fts, 0.0, 0.0, ?, ?) AS lexical_score
            FROM chunks_fts
            JOIN chunks AS c ON c.id = CAST(chunks_fts.chunk_id AS INTEGER)
            JOIN articles AS a ON a.id = c.article_id
            LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
            LEFT JOIN chunk_corrections AS correction
              ON correction.chunk_id = c.id AND correction.state = 'approved'
            WHERE {" AND ".join(predicates)}
            ORDER BY lexical_score, c.id
            LIMIT ?
        """
        owns_connection = connection is None
        active_connection = connection or self.connect()
        try:
            rows = [*active_connection.execute(sql, parameters)]
            # A session verifies this once and rechecks after a writer commits. A one-off
            # query intentionally keeps the historical behaviour and searches captions.
            if caption_index_has_rows is not False:
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
                        l.locator_kind,
                        CASE WHEN l.locator_kind = 'structural' THEN NULL
                             ELSE COALESCE(l.page_start, c.page_start) END AS locator_page_start,
                        CASE WHEN l.locator_kind = 'structural' THEN NULL
                             ELSE COALESCE(l.page_end, c.page_end) END AS locator_page_end,
                        l.section_path, l.paragraph_start, l.paragraph_end,
                        l.xml_id_start, l.xml_id_end,
                        a.title AS article_title,
                        a.publication_year,
                        bm25(document_element_captions_fts) + 0.25 AS lexical_score
                    FROM document_element_captions_fts
                    JOIN document_elements AS d
                      ON d.rowid = document_element_captions_fts.rowid
                    JOIN document_element_relations AS r ON r.element_id = d.id
                    JOIN chunks AS c ON c.id = r.related_chunk_id
                    JOIN articles AS a ON a.id = c.article_id
                    LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                    WHERE {" AND ".join(caption_predicates)}
                    ORDER BY lexical_score, c.id
                    LIMIT ?
                """
                rows.extend(active_connection.execute(caption_sql, caption_parameters))
        finally:
            if owns_connection:
                active_connection.close()
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
            article_predicate = f" AND c.article_id IN ({article_placeholders})"
            article_parameters = unique_articles
        sql = f"""
            SELECT c.id, c.article_id, c.section, c.page_start, c.page_end,
                   COALESCE(correction.corrected_text, c.text) AS text
            FROM chunks AS c
            JOIN articles AS a ON a.id = c.article_id
            LEFT JOIN chunk_corrections AS correction
              ON correction.chunk_id = c.id AND correction.state = 'approved'
            WHERE c.id > ? AND c.embedding_status IN ({placeholders})
              AND a.validation_status IN ('validated', 'indexed')
              {article_predicate}
            ORDER BY c.id
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

    def list_articles(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        if limit is not None and limit < 1:
            raise ValueError("article list limit must be positive")
        limit_clause = "" if limit is None else "LIMIT ?"
        parameters: tuple[int, ...] = () if limit is None else (limit,)
        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT
                        a.id, a.title, a.doi, a.journal, a.work_type, a.publisher,
                        a.publication_year,
                        a.language, a.validation_status, a.pdf_path, a.source,
                        a.created_at, a.indexed_at,
                        COUNT(c.id) AS chunk_count,
                        SUM(CASE WHEN c.embedding_status = 'indexed' THEN 1 ELSE 0 END)
                            AS indexed_chunk_count
                    FROM articles AS a
                    LEFT JOIN chunks AS c ON c.article_id = a.id
                    GROUP BY a.id
                    ORDER BY a.created_at DESC, a.id
                    {limit_clause}
                    """,
                    parameters,
                )
            )

    def upsert_ascocid_wiki_document(
        self,
        *,
        document_id: str,
        relative_path: str,
        filename: str,
        source_sha256: str,
        article_id: str,
        indexed_file_path: str,
        indexed_file_sha256: str,
        state: Literal["indexed", "review", "failed"] = "indexed",
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Link one versioned Ascocid source file to SQLite-authoritative evidence."""

        cleaned_id = _validate_extraction_run_text(
            document_id, field_name="Ascocid document ID", maximum=128
        )
        cleaned_relative_path = _validate_extraction_run_text(
            relative_path, field_name="Ascocid relative path", maximum=4_000
        )
        cleaned_filename = _validate_extraction_run_text(
            filename, field_name="Ascocid filename", maximum=500
        )
        cleaned_article_id = _validate_extraction_run_text(
            article_id, field_name="Ascocid article ID", maximum=300
        )
        cleaned_indexed_path = _validate_extraction_run_text(
            indexed_file_path, field_name="Ascocid indexed file path", maximum=4_000
        )
        _validate_extraction_run_hash(source_sha256, field_name="Ascocid source SHA-256")
        _validate_extraction_run_hash(
            indexed_file_sha256, field_name="Ascocid indexed file SHA-256"
        )
        if state not in {"indexed", "review", "failed"}:
            raise ValueError("Ascocid document state is invalid")
        if (error_type is None) != (error_message is None):
            raise ValueError("Ascocid diagnostics must be both present or absent")
        if error_type is not None and error_message is not None:
            error_type, error_message = _validate_extraction_run_diagnostic(
                error_type, error_message
            )
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO ascocid_wiki_documents(
                    id, relative_path, filename, source_sha256, article_id,
                    indexed_file_path, indexed_file_sha256, state, error_type, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    filename = excluded.filename,
                    source_sha256 = excluded.source_sha256,
                    article_id = excluded.article_id,
                    indexed_file_path = excluded.indexed_file_path,
                    indexed_file_sha256 = excluded.indexed_file_sha256,
                    state = excluded.state,
                    error_type = excluded.error_type,
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    cleaned_id,
                    cleaned_relative_path,
                    cleaned_filename,
                    source_sha256,
                    cleaned_article_id,
                    cleaned_indexed_path,
                    indexed_file_sha256,
                    state,
                    error_type,
                    error_message,
                ),
            )

    def ascocid_wiki_documents(self, article_ids: Sequence[str] | None = None) -> list[sqlite3.Row]:
        """Return active Ascocid aliases in deterministic citation order."""

        predicate = "WHERE w.state = 'indexed'"
        parameters: list[str] = []
        if article_ids is not None:
            unique = list(dict.fromkeys(article_ids))
            if not unique:
                return []
            placeholders = ",".join("?" for _ in unique)
            predicate += f" AND w.article_id IN ({placeholders})"
            parameters.extend(unique)
        with closing(self.connect()) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                ("ascocid_wiki_documents",),
            ).fetchone()
            if table_exists is None:
                return []
            return list(
                connection.execute(
                    f"""
                    SELECT w.*, a.validation_status
                    FROM ascocid_wiki_documents AS w
                    JOIN articles AS a ON a.id = w.article_id
                    {predicate}
                      AND a.validation_status IN ('validated', 'indexed')
                    ORDER BY w.article_id, w.relative_path
                    """,
                    parameters,
                )
            )

    def ascocid_wiki_article_ids(self) -> list[str]:
        """Return distinct searchable article IDs represented in the Ascocid wiki."""

        return list(dict.fromkeys(str(row["article_id"]) for row in self.ascocid_wiki_documents()))

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
            article = connection.execute(
                "SELECT validation_status FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
            if article is None:
                raise ValueError("article is unavailable")
            if str(article["validation_status"]) not in {"validated", "indexed"}:
                raise ValueError("article is excluded from retrieval")
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

    def unidentifiable_local_articles(self) -> list[sqlite3.Row]:
        """Return only local sources carrying the explicit no-title fallback."""

        with closing(self.connect()) as connection:
            return list(
                connection.execute(
                    """
                    SELECT a.id, a.sha256, a.title, a.pdf_path, a.validation_status,
                           COUNT(c.id) AS chunk_count
                    FROM articles AS a
                    LEFT JOIN chunks AS c ON c.article_id = a.id
                    WHERE lower(trim(a.source)) = 'local'
                      AND lower(trim(a.title)) = 'fichier local'
                    GROUP BY a.id
                    ORDER BY a.id
                    """
                )
            )

    def exclude_unidentifiable_local_articles(
        self,
        article_ids: Sequence[str],
        *,
        reason: str,
    ) -> int:
        """Exclude explicit local-title fallbacks while retaining files and provenance."""

        unique_ids = tuple(dict.fromkeys(article_ids))
        cleaned_reason = " ".join(reason.split())
        if not unique_ids:
            return 0
        if not cleaned_reason:
            raise ValueError("retrieval exclusion reason is required")
        placeholders = ",".join("?" for _ in unique_ids)
        with self.transaction() as connection:
            rows = list(
                connection.execute(
                    f"""
                    SELECT id, source, title
                    FROM articles
                    WHERE id IN ({placeholders})
                    """,
                    unique_ids,
                )
            )
            if len(rows) != len(unique_ids):
                raise ValueError("one or more articles are unavailable")
            if any(
                str(row["source"]).strip().lower() != "local"
                or str(row["title"]).strip().lower() != "fichier local"
                for row in rows
            ):
                raise ValueError("only unidentifiable local articles can be excluded")
            connection.executemany(
                """
                INSERT INTO article_retrieval_exclusions(article_id, reason)
                VALUES (?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    reason = excluded.reason,
                    excluded_at = CURRENT_TIMESTAMP
                """,
                [(article_id, cleaned_reason) for article_id in unique_ids],
            )
            cursor = connection.execute(
                f"""
                UPDATE articles
                SET validation_status = 'rejected', indexed_at = NULL
                WHERE id IN ({placeholders})
                  AND validation_status != 'rejected'
                """,
                unique_ids,
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
                    COALESCE(correction.corrected_text, c.text) AS effective_text,
                    l.locator_kind,
                    CASE WHEN l.locator_kind = 'structural' THEN NULL
                         ELSE COALESCE(l.page_start, c.page_start) END AS locator_page_start,
                    CASE WHEN l.locator_kind = 'structural' THEN NULL
                         ELSE COALESCE(l.page_end, c.page_end) END AS locator_page_end,
                    l.section_path, l.paragraph_start, l.paragraph_end,
                    l.xml_id_start, l.xml_id_end,
                    a.title AS article_title,
                    a.publication_year,
                    a.language AS article_language
                FROM chunks AS c
                JOIN articles AS a ON a.id = c.article_id
                LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                LEFT JOIN chunk_corrections AS correction
                  ON correction.chunk_id = c.id AND correction.state = 'approved'
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
                    id, sha256, doi, title, abstract, authors, journal, publication_year,
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

    def hierarchical_chunks_for_article(
        self,
        article_id: str,
        *,
        anchor_chunk_ids: Sequence[int],
        neighborhood_radius: int = 1,
        limit: int = 20,
        include_methods: bool = False,
    ) -> list[sqlite3.Row]:
        """Navigate article -> section -> chunk without scanning the complete article.

        The returned rows are the original SQLite chunks. Ranked anchors come first,
        followed by their local neighbours, chunks from the same sections, then a
        small preferred-section fallback. No summary or generated text is introduced.
        """

        if limit <= 0:
            raise ValueError("hierarchical article chunk limit must be positive")
        if neighborhood_radius < 0:
            raise ValueError("hierarchical chunk radius cannot be negative")
        anchors = list(dict.fromkeys(int(chunk_id) for chunk_id in anchor_chunk_ids))[:8]
        with closing(self.connect()) as connection:
            article = connection.execute(
                """
                SELECT id
                FROM articles
                WHERE id = ? AND validation_status IN ('validated', 'indexed')
                """,
                (article_id,),
            ).fetchone()
            if article is None:
                return []

            anchor_rows: list[sqlite3.Row] = []
            if anchors:
                placeholders = ",".join("?" for _ in anchors)
                anchor_rows = list(
                    connection.execute(
                        f"""
                        SELECT c.*, l.locator_kind, l.section_path, l.paragraph_start,
                               l.paragraph_end, l.xml_id_start, l.xml_id_end
                        FROM chunks AS c
                        LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                        WHERE c.article_id = ? AND c.id IN ({placeholders})
                        """,
                        (article_id, *anchors),
                    )
                )
                by_id = {int(row["id"]): row for row in anchor_rows}
                anchor_rows = [by_id[chunk_id] for chunk_id in anchors if chunk_id in by_id]

            ordered: list[sqlite3.Row] = []
            seen: set[int] = set()

            def append_rows(rows: Sequence[sqlite3.Row]) -> None:
                for row in rows:
                    chunk_id = int(row["id"])
                    if chunk_id in seen or len(ordered) >= limit:
                        continue
                    ordered.append(row)
                    seen.add(chunk_id)

            append_rows(anchor_rows)
            anchor_indexes = [int(row["chunk_index"]) for row in anchor_rows]
            if anchor_indexes and len(ordered) < limit:
                predicates = " OR ".join("c.chunk_index BETWEEN ? AND ?" for _ in anchor_indexes)
                parameters: list[Any] = [article_id]
                for index in anchor_indexes:
                    parameters.extend(
                        [max(0, index - neighborhood_radius), index + neighborhood_radius]
                    )
                neighbour_rows = list(
                    connection.execute(
                        f"""
                        SELECT c.*, l.locator_kind, l.section_path, l.paragraph_start,
                               l.paragraph_end, l.xml_id_start, l.xml_id_end
                        FROM chunks AS c
                        LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                        WHERE c.article_id = ? AND ({predicates})
                        ORDER BY c.chunk_index
                        """,
                        parameters,
                    )
                )
                neighbour_rows.sort(
                    key=lambda row: (
                        min(
                            abs(int(row["chunk_index"]) - anchor_index)
                            for anchor_index in anchor_indexes
                        ),
                        int(row["chunk_index"]),
                    )
                )
                append_rows(neighbour_rows)

            anchor_sections = list(
                dict.fromkeys(
                    str(row["section"])
                    for row in anchor_rows
                    if row["section"] is not None and str(row["section"]).strip()
                )
            )
            preferred_sections = ["Results", "Discussion", "Conclusion", "Abstract"]
            if include_methods:
                preferred_sections.append("Materials and Methods")
            section_names = list(dict.fromkeys([*anchor_sections, *preferred_sections]))
            if section_names and len(ordered) < limit:
                placeholders = ",".join("?" for _ in section_names)
                section_rows = list(
                    connection.execute(
                        f"""
                        SELECT c.*, l.locator_kind, l.section_path, l.paragraph_start,
                               l.paragraph_end, l.xml_id_start, l.xml_id_end
                        FROM chunks AS c
                        LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                        WHERE c.article_id = ?
                          AND lower(COALESCE(c.section, '')) IN ({placeholders})
                        ORDER BY
                            CASE lower(COALESCE(c.section, ''))
                                WHEN 'results' THEN 0
                                WHEN 'discussion' THEN 1
                                WHEN 'conclusion' THEN 2
                                WHEN 'abstract' THEN 3
                                WHEN 'materials and methods' THEN 4
                                ELSE 5
                            END,
                            c.chunk_index
                        LIMIT ?
                        """,
                        (
                            article_id,
                            *(section.casefold() for section in section_names),
                            max(limit * 2, limit),
                        ),
                    )
                )
                append_rows(section_rows)

            if len(ordered) < limit:
                fallback_rows = list(
                    connection.execute(
                        """
                        SELECT c.*, l.locator_kind, l.section_path, l.paragraph_start,
                               l.paragraph_end, l.xml_id_start, l.xml_id_end
                        FROM chunks AS c
                        LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                        WHERE c.article_id = ?
                        ORDER BY
                            CASE lower(COALESCE(c.section, ''))
                                WHEN 'results' THEN 0
                                WHEN 'discussion' THEN 1
                                WHEN 'conclusion' THEN 2
                                WHEN 'abstract' THEN 3
                                WHEN 'introduction' THEN 4
                                WHEN 'materials and methods' THEN 6
                                ELSE 5
                            END,
                            c.chunk_index
                        LIMIT ?
                        """,
                        (article_id, limit),
                    )
                )
                append_rows(fallback_rows)
            return ordered[:limit]

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
                    f"""
                    SELECT c.*, l.locator_kind, l.section_path, l.paragraph_start,
                           l.paragraph_end, l.xml_id_start, l.xml_id_end
                    FROM chunks AS c LEFT JOIN chunk_locators AS l ON l.chunk_id = c.id
                    WHERE c.id IN ({",".join("?" for _ in allowed_ids)})
                    """,
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
                locator_kind = str(row["locator_kind"] or "page")
                if finding.locator_kind != locator_kind:
                    raise ValueError("finding locator kind differs from SQLite")
                if locator_kind == "page" and (
                    row["page_start"] != finding.page_start or row["page_end"] != finding.page_end
                ):
                    raise ValueError("finding pages differ from SQLite")
                if locator_kind == "structural" and (
                    row["section_path"] != finding.section_path
                    or row["paragraph_start"] != finding.paragraph_start
                    or row["paragraph_end"] != finding.paragraph_end
                    or row["xml_id_start"] != finding.xml_id_start
                    or row["xml_id_end"] != finding.xml_id_end
                ):
                    raise ValueError("finding structural locator differs from SQLite")
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
                    page_start, page_end, locator_kind, section_path, paragraph_start,
                    paragraph_end, xml_id_start, xml_id_end, relevance_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        finding.locator_kind,
                        finding.section_path,
                        finding.paragraph_start,
                        finding.paragraph_end,
                        finding.xml_id_start,
                        finding.xml_id_end,
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
                    SELECT claim, source_excerpt, page_start, page_end, locator_kind,
                           section_path, paragraph_start, paragraph_end,
                           xml_id_start, xml_id_end, chunk_id
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
                        "locator_kind": row["locator_kind"],
                        "section_path": row["section_path"],
                        "paragraph_start": row["paragraph_start"],
                        "paragraph_end": row["paragraph_end"],
                        "xml_id_start": row["xml_id_start"],
                        "xml_id_end": row["xml_id_end"],
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
            cursor = connection.execute(
                """
                UPDATE chunks
                SET embedding_status = 'pending'
                WHERE EXISTS (
                    SELECT 1 FROM articles
                    WHERE articles.id = chunks.article_id
                      AND articles.validation_status IN ('validated', 'indexed')
                )
                """
            )
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
