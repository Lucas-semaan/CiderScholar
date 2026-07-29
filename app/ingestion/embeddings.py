"""Local-only multilingual embeddings processed in bounded sequential batches."""

from __future__ import annotations

import gc
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.database.sqlite import Database
from app.memory import MemoryGuard

LOGGER = logging.getLogger(__name__)


class LocalEmbeddingModelNotFoundError(FileNotFoundError):
    """Raised instead of silently contacting a model registry."""


def model_storage_name(model_name: str) -> str:
    """Map a registry identifier to one deterministic local directory name."""

    source = model_name.strip()
    if any(part in {"", ".", ".."} for part in re.split(r"[/\\]", source)):
        raise ValueError("invalid embedding model name")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "--", source)
    if not normalized or normalized in {".", ".."}:
        raise ValueError("invalid embedding model name")
    return normalized


def local_model_path(settings: Settings, model_name: str | None = None) -> Path:
    selected = model_name or settings.embeddings.model_name
    return settings.paths.models_dir / model_storage_name(selected)


def prepare_prefixed_texts(texts: Sequence[str], prefix: str) -> list[str]:
    """Normalize whitespace and apply the E5 task prefix exactly once."""

    prepared: list[str] = []
    for text in texts:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            raise ValueError("cannot embed empty text")
        prepared.append(normalized if normalized.startswith(prefix) else f"{prefix}{normalized}")
    return prepared


class EmbeddingBackend(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def encode_documents(self, texts: Sequence[str]) -> Any: ...

    def encode_queries(self, texts: Sequence[str]) -> Any: ...

    def close(self) -> None: ...


class SentenceTransformerBackend:
    """Lazy Sentence Transformers backend that can only open a local model directory."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_name: str | None = None,
        model_path: str | Path | None = None,
    ) -> None:
        self.settings = settings
        self._model_name = model_name or settings.embeddings.model_name
        self.path = (
            Path(model_path).resolve()
            if model_path is not None
            else local_model_path(settings, self._model_name).resolve()
        )
        self._model: Any | None = None
        self._dimension: int | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if not self.path.is_dir():
            raise LocalEmbeddingModelNotFoundError(
                f"local embedding model not found: {self.path}. "
                "Run: python -m scripts.prepare_embedding_model --allow-network"
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - installation concern
            raise RuntimeError("sentence-transformers is required for embeddings") from exc

        LOGGER.info(
            "Loading local embedding model path=%s device=%s",
            self.path,
            self.settings.embeddings.device,
        )
        self._model = SentenceTransformer(
            str(self.path),
            device=self.settings.embeddings.device,
            local_files_only=True,
            trust_remote_code=False,
        )
        self._model.max_seq_length = self.settings.embeddings.max_sequence_length
        dimension_getter = getattr(self._model, "get_embedding_dimension", None)
        if dimension_getter is None:  # Compatibility with older 5.x releases.
            dimension_getter = self._model.get_sentence_embedding_dimension
        dimension = dimension_getter()
        if dimension is None or int(dimension) <= 0:
            self.close()
            raise RuntimeError("embedding model did not expose a valid vector dimension")
        self._dimension = int(dimension)
        return self._model

    @property
    def dimension(self) -> int:
        self._load()
        assert self._dimension is not None
        return self._dimension

    def _encode(self, texts: Sequence[str], prefix: str) -> Any:
        if not texts:
            raise ValueError("embedding batch cannot be empty")
        model = self._load()
        prepared = prepare_prefixed_texts(texts, prefix)
        vectors = model.encode(
            prepared,
            batch_size=min(len(prepared), self.settings.embeddings.batch_size),
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=self.settings.embeddings.normalize,
            precision="float32",
        )
        return vectors.astype("float32", copy=False)

    def encode_documents(self, texts: Sequence[str]) -> Any:
        return self._encode(texts, self.settings.embeddings.passage_prefix)

    def encode_queries(self, texts: Sequence[str]) -> Any:
        return self._encode(texts, self.settings.embeddings.query_prefix)

    def close(self) -> None:
        model = self._model
        self._model = None
        self._dimension = None
        if model is not None:
            del model
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


@dataclass(frozen=True, slots=True)
class EmbeddedChunkBatch:
    chunk_ids: tuple[int, ...]
    article_ids: tuple[str, ...]
    sections: tuple[str | None, ...]
    page_starts: tuple[int, ...]
    page_ends: tuple[int, ...]
    vectors: Any
    model_name: str
    vector_dimension: int


class EmbeddingSink(Protocol):
    """Durable vector destinations, implemented by local Qdrant next."""

    def upsert(self, batch: EmbeddedChunkBatch) -> None: ...


class EmbeddingRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str
    batch_size: int
    batches_completed: int = Field(default=0, ge=0)
    chunks_indexed: int = Field(default=0, ge=0)
    chunks_failed: int = Field(default=0, ge=0)
    recovered_processing_chunks: int = Field(default=0, ge=0)
    peak_process_rss_gb: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    error_type: str | None = None
    error_message: str | None = None


def _matrix_shape(vectors: Any) -> tuple[int, int]:
    shape = getattr(vectors, "shape", None)
    if shape is not None and len(shape) == 2:
        return int(shape[0]), int(shape[1])
    rows = len(vectors)
    columns = len(vectors[0]) if rows else 0
    if any(len(vector) != columns for vector in vectors):
        raise ValueError("embedding backend returned ragged vectors")
    return rows, columns


class EmbeddingBatchProcessor:
    """Encode and persist pending chunks without loading the corpus at once."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        backend: EmbeddingBackend,
    ) -> None:
        self.settings = settings
        self.database = database
        self.backend = backend
        self.memory = MemoryGuard(settings.memory)

    def run(
        self,
        sink: EmbeddingSink,
        *,
        retry_failed: bool = False,
        stop_on_error: bool = True,
        close_backend: bool = True,
        article_ids: Sequence[str] | None = None,
    ) -> EmbeddingRunReport:
        started = datetime.now(UTC)
        recovered = self.database.reset_processing_embeddings(article_ids)
        report = EmbeddingRunReport(
            model_name=self.backend.model_name,
            batch_size=self.settings.embeddings.batch_size,
            recovered_processing_chunks=recovered,
        )
        after_id = 0
        try:
            while rows := self.database.chunks_for_embedding(
                after_id=after_id,
                limit=self.settings.embeddings.batch_size,
                retry_failed=retry_failed,
                article_ids=article_ids,
            ):
                chunk_ids = tuple(int(row["id"]) for row in rows)
                after_id = chunk_ids[-1]
                self.memory.check("embedding batch")
                self.database.update_embedding_status(chunk_ids, "processing")
                try:
                    vectors = self.backend.encode_documents([str(row["text"]) for row in rows])
                    vector_rows, vector_dimension = _matrix_shape(vectors)
                    if vector_rows != len(rows) or vector_dimension != self.backend.dimension:
                        raise ValueError("embedding backend returned an unexpected matrix shape")
                    sink.upsert(
                        EmbeddedChunkBatch(
                            chunk_ids=chunk_ids,
                            article_ids=tuple(str(row["article_id"]) for row in rows),
                            sections=tuple(row["section"] for row in rows),
                            page_starts=tuple(int(row["page_start"]) for row in rows),
                            page_ends=tuple(int(row["page_end"]) for row in rows),
                            vectors=vectors,
                            model_name=self.backend.model_name,
                            vector_dimension=vector_dimension,
                        )
                    )
                    self.database.update_embedding_status(chunk_ids, "indexed")
                    report.batches_completed += 1
                    report.chunks_indexed += len(chunk_ids)
                    snapshot = self.memory.check("embedding persistence")
                    if snapshot is not None:
                        report.peak_process_rss_gb = max(
                            report.peak_process_rss_gb, snapshot.process_rss_gb
                        )
                except Exception as exc:
                    self.database.update_embedding_status(chunk_ids, "failed")
                    report.chunks_failed += len(chunk_ids)
                    report.error_type = type(exc).__name__
                    report.error_message = str(exc)[:1000]
                    LOGGER.error(
                        "Embedding batch failed first_chunk_id=%s count=%s error_type=%s",
                        chunk_ids[0],
                        len(chunk_ids),
                        type(exc).__name__,
                    )
                    if stop_on_error:
                        break
        finally:
            if report.chunks_indexed:
                self.database.refresh_fully_indexed_articles()
            if close_backend:
                self.backend.close()
            report.duration_seconds = (datetime.now(UTC) - started).total_seconds()
        return report
