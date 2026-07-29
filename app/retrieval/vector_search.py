"""Embedded Qdrant vector storage and SQLite-hydrated semantic search."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient, models

from app.config import Settings
from app.corpora import CorpusScope
from app.database.sqlite import Database
from app.ingestion.embeddings import (
    EmbeddedChunkBatch,
    EmbeddingBackend,
)
from app.resource_lock import ResourceFileLock, corpus_resource_lock_path

LOGGER = logging.getLogger(__name__)


class VectorIndexConfigurationError(RuntimeError):
    """Raised when an existing collection cannot accept the configured model."""


class VectorIndexCorruptionError(RuntimeError):
    """Raised when a Qdrant point no longer maps cleanly to SQLite."""


class ScoredChunkReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: int = Field(gt=0)
    article_id: str
    score: float


class VectorSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: int = Field(gt=0)
    article_id: str
    score: float
    section: str | None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str
    scope: CorpusScope = CorpusScope.COMMON


def _float_vector(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    elif not isinstance(vector, (str, bytes)):
        vector = list(vector)
    if not isinstance(vector, list) or any(isinstance(value, (list, tuple)) for value in vector):
        raise ValueError("expected one flat vector")
    return [float(value) for value in vector]


class QdrantLocalIndex:
    """Qdrant local mode only: no URL, port, API key, or remote inference."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_name: str | None = None,
        path: str | Path | None = None,
        collection_name: str | None = None,
    ) -> None:
        self.settings = settings
        self.model_name = model_name or settings.embeddings.model_name
        self.path = (
            Path(path).resolve() if path is not None else settings.paths.qdrant_dir.resolve()
        )
        self.collection_name = collection_name or settings.qdrant.collection_name
        self._client: QdrantClient | None = None
        self._resource_lock: ResourceFileLock | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self.path.mkdir(parents=True, exist_ok=True)
            lock = ResourceFileLock(corpus_resource_lock_path(self.path))
            lock.acquire()
            try:
                self._client = QdrantClient(
                    path=str(self.path),
                    # Access is serialized by the application. This also avoids qdrant-client's
                    # temporary `:memory:` SQLite thread probe, whose context manager does not close
                    # the connection under Python 3.14.
                    force_disable_check_same_thread=True,
                    cloud_inference=False,
                )
            except Exception:
                lock.release()
                raise
            self._resource_lock = lock
        return self._client

    def __enter__(self) -> QdrantLocalIndex:
        _ = self.client
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None
        finally:
            if self._resource_lock is not None:
                self._resource_lock.release()
                self._resource_lock = None

    def collection_exists(self) -> bool:
        return self.client.collection_exists(self.collection_name)

    def ensure_collection(self, vector_dimension: int) -> None:
        if vector_dimension <= 0:
            raise ValueError("vector dimension must be positive")
        if not self.collection_exists():
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=vector_dimension,
                    distance=models.Distance.COSINE,
                    on_disk=self.settings.qdrant.on_disk_vectors,
                ),
                on_disk_payload=self.settings.qdrant.on_disk_payload,
                metadata={
                    "model_name": self.model_name,
                    "vector_dimension": vector_dimension,
                    "managed_by": "local-science-rag",
                },
            )
            LOGGER.info(
                "Created local Qdrant collection name=%s dimension=%s",
                self.collection_name,
                vector_dimension,
            )
            return

        info = self.client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        if not isinstance(vectors, models.VectorParams):
            raise VectorIndexConfigurationError("named vectors are not supported by this index")
        metadata = info.config.metadata or {}
        actual_model = metadata.get("model_name")
        if int(vectors.size) != vector_dimension:
            raise VectorIndexConfigurationError(
                f"collection dimension is {vectors.size}, expected {vector_dimension}"
            )
        if vectors.distance != models.Distance.COSINE:
            raise VectorIndexConfigurationError("collection distance must be cosine")
        if actual_model != self.model_name:
            raise VectorIndexConfigurationError(
                f"collection model is {actual_model!r}, expected {self.model_name!r}"
            )

    def upsert(self, batch: EmbeddedChunkBatch) -> None:
        if batch.model_name != self.model_name:
            raise VectorIndexConfigurationError(
                f"batch model is {batch.model_name!r}, expected {self.model_name!r}"
            )
        sizes = {
            len(batch.chunk_ids),
            len(batch.article_ids),
            len(batch.sections),
            len(batch.page_starts),
            len(batch.page_ends),
            len(batch.vectors),
        }
        if sizes != {len(batch.chunk_ids)} or not batch.chunk_ids:
            raise ValueError("inconsistent or empty embedded chunk batch")
        self.ensure_collection(batch.vector_dimension)

        points: list[models.PointStruct] = []
        for index, chunk_id in enumerate(batch.chunk_ids):
            vector = _float_vector(batch.vectors[index])
            if len(vector) != batch.vector_dimension:
                raise ValueError("vector dimension does not match batch metadata")
            points.append(
                models.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload={
                        "kind": "chunk",
                        "chunk_id": chunk_id,
                        "article_id": batch.article_ids[index],
                        "section": batch.sections[index],
                        "page_start": batch.page_starts[index],
                        "page_end": batch.page_ends[index],
                        "model_name": batch.model_name,
                    },
                )
            )
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    def search(
        self,
        query_vector: Sequence[float] | Any,
        *,
        limit: int | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[ScoredChunkReference]:
        vector = _float_vector(query_vector)
        search_limit = self.settings.qdrant.default_search_limit if limit is None else limit
        if search_limit <= 0:
            raise ValueError("vector search limit must be positive")
        if not self.collection_exists():
            return []
        self.ensure_collection(len(vector))
        conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="kind", match=models.MatchValue(value="chunk"))
        ]
        if article_ids is not None:
            unique_ids = list(dict.fromkeys(article_ids))
            if not unique_ids:
                return []
            conditions.append(
                models.FieldCondition(key="article_id", match=models.MatchAny(any=unique_ids))
            )
        if sections is not None:
            unique_sections = list(dict.fromkeys(sections))
            if not unique_sections:
                return []
            conditions.append(
                models.FieldCondition(key="section", match=models.MatchAny(any=unique_sections))
            )
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=conditions),
            limit=search_limit,
            with_payload=True,
            with_vectors=False,
            score_threshold=(
                score_threshold
                if score_threshold is not None
                else self.settings.qdrant.score_threshold
            ),
        )
        results: list[ScoredChunkReference] = []
        for point in response.points:
            payload = point.payload or {}
            chunk_id = payload.get("chunk_id")
            article_id = payload.get("article_id")
            if not isinstance(chunk_id, int) or not isinstance(article_id, str):
                raise VectorIndexCorruptionError("Qdrant point has invalid chunk payload")
            if int(point.id) != chunk_id:
                raise VectorIndexCorruptionError("Qdrant point id differs from chunk id")
            results.append(
                ScoredChunkReference(
                    chunk_id=chunk_id,
                    article_id=article_id,
                    score=float(point.score),
                )
            )
        return results

    def count(self) -> int:
        if not self.collection_exists():
            return 0
        return int(
            self.client.count(
                collection_name=self.collection_name,
                count_filter=models.Filter(
                    must=[models.FieldCondition(key="kind", match=models.MatchValue(value="chunk"))]
                ),
                exact=True,
            ).count
        )

    def delete_points(self, chunk_ids: Sequence[int]) -> int:
        """Synchronously delete only explicit SQLite chunk point IDs."""

        unique_ids = list(dict.fromkeys(chunk_ids))
        if not unique_ids or not self.collection_exists():
            return 0
        if any(chunk_id <= 0 for chunk_id in unique_ids):
            raise ValueError("Qdrant chunk identifiers must be positive")
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=unique_ids),
            wait=True,
        )
        return len(unique_ids)

    def delete_collection(self) -> bool:
        if not self.collection_exists():
            return False
        return bool(self.client.delete_collection(self.collection_name))


class VectorSearchService:
    """Encode a question, search Qdrant, then hydrate authoritative text from SQLite."""

    def __init__(
        self,
        database: Database,
        backend: EmbeddingBackend,
        index: QdrantLocalIndex,
    ) -> None:
        self.database = database
        self.backend = backend
        self.index = index

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> list[VectorSearchResult]:
        vectors = self.backend.encode_queries([query])
        references = self.index.search(
            vectors[0], limit=limit, article_ids=article_ids, sections=sections
        )
        chunks = self.database.chunks_by_ids([reference.chunk_id for reference in references])
        results: list[VectorSearchResult] = []
        for reference in references:
            chunk = chunks.get(reference.chunk_id)
            if chunk is None:
                raise VectorIndexCorruptionError(
                    f"chunk {reference.chunk_id} exists in Qdrant but not SQLite"
                )
            if str(chunk["article_id"]) != reference.article_id:
                raise VectorIndexCorruptionError(
                    f"chunk {reference.chunk_id} has inconsistent article id"
                )
            results.append(
                VectorSearchResult(
                    chunk_id=reference.chunk_id,
                    article_id=reference.article_id,
                    score=reference.score,
                    section=chunk["section"],
                    page_start=int(chunk["page_start"]),
                    page_end=int(chunk["page_end"]),
                    text=str(chunk["text"]),
                )
            )
        return results

    def close(self) -> None:
        self.backend.close()
        self.index.close()
