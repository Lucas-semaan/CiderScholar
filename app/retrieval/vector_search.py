"""Embedded Qdrant vector storage and SQLite-hydrated semantic search."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import QdrantClient, models

from app.config import Settings
from app.corpora import CorpusScope
from app.database.sqlite import Database
from app.desktop.model_integrity import ModelIntegrityError
from app.ingestion.embeddings import (
    EmbeddedChunkBatch,
    EmbeddingBackend,
    SentenceTransformerBackend,
)
from app.resource_lock import ResourceFileLock, corpus_resource_lock_path
from app.retrieval.index_manifest import (
    assert_index_generation_mutable,
    assert_index_generation_ready,
)

LOGGER = logging.getLogger(__name__)
_QUERY_VECTOR_CACHE_LIMIT = 128
_QUERY_VECTOR_CACHE: OrderedDict[tuple[str, str], Any] = OrderedDict()
_QUERY_VECTOR_CACHE_LOCK = threading.Lock()
_LEGACY_MANIFEST_WARNINGS: set[tuple[str, str]] = set()
_LEGACY_MANIFEST_WARNINGS_LOCK = threading.Lock()


def clear_query_vector_cache() -> None:
    """Clear the bounded process-local cache, primarily for deterministic tests."""

    with _QUERY_VECTOR_CACHE_LOCK:
        _QUERY_VECTOR_CACHE.clear()


def _cached_query_vector(model_name: str, query: str) -> Any | None:
    cache_key = (model_name, sha256(query.strip().encode("utf-8")).hexdigest())
    with _QUERY_VECTOR_CACHE_LOCK:
        vector = _QUERY_VECTOR_CACHE.get(cache_key)
        if vector is not None:
            _QUERY_VECTOR_CACHE.move_to_end(cache_key)
        return vector


def _remember_query_vector(model_name: str, query: str, vector: Any) -> None:
    cache_key = (model_name, sha256(query.strip().encode("utf-8")).hexdigest())
    stored = vector.copy() if hasattr(vector, "copy") else list(vector)
    with _QUERY_VECTOR_CACHE_LOCK:
        _QUERY_VECTOR_CACHE[cache_key] = stored
        _QUERY_VECTOR_CACHE.move_to_end(cache_key)
        while len(_QUERY_VECTOR_CACHE) > _QUERY_VECTOR_CACHE_LIMIT:
            _QUERY_VECTOR_CACHE.popitem(last=False)


def _warn_legacy_index_once(index: QdrantLocalIndex) -> None:
    key = (str(index.path), index.collection_name)
    with _LEGACY_MANIFEST_WARNINGS_LOCK:
        if key in _LEGACY_MANIFEST_WARNINGS:
            return
        _LEGACY_MANIFEST_WARNINGS.add(key)
    LOGGER.warning(
        "Using legacy_unverified vector index collection=%s path=%s; run --recreate to adopt "
        "a versioned generation manifest",
        index.collection_name,
        index.path,
    )


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
        assert_index_generation_mutable(self)
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
        _manifest_validated: bool = False,
    ) -> list[ScoredChunkReference]:
        if not _manifest_validated:
            assert_index_generation_ready(self)
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
        assert_index_generation_mutable(self)
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=unique_ids),
            wait=True,
        )
        return len(unique_ids)

    def delete_collection(self) -> bool:
        if not self.collection_exists():
            return False
        assert_index_generation_mutable(self)
        client = self.client
        local_client = getattr(client, "_client", None)
        collection_path: Path | None = None
        path_factory = getattr(local_client, "_collection_path", None)
        if callable(path_factory):
            raw_path = path_factory(self.collection_name)
            collection_path = Path(raw_path) if raw_path is not None else None

        # qdrant-client local mode removes the collection directory without
        # closing its SQLite handle first.  Windows then leaves the directory
        # in place silently, and a later create_collection reopens stale
        # points.  Close only the selected local collection before delegating
        # to the client; remote clients do not expose this mapping.
        collections = getattr(local_client, "collections", None)
        if isinstance(collections, dict):
            collection = collections.get(self.collection_name)
            close_collection = getattr(collection, "close", None)
            if callable(close_collection):
                close_collection()

        deleted = bool(client.delete_collection(self.collection_name))
        if deleted and collection_path is not None and collection_path.exists():
            raise VectorIndexConfigurationError(
                f"Qdrant collection directory still exists after deletion: {collection_path}"
            )
        return deleted


class VectorSearchService:
    """Encode a question, search Qdrant, then hydrate authoritative text from SQLite."""

    def __init__(
        self,
        database: Database,
        backend: EmbeddingBackend,
        index: QdrantLocalIndex,
        *,
        close_backend: bool = True,
    ) -> None:
        self.database = database
        self.backend = backend
        self.index = index
        self.close_backend = close_backend
        self.query_cache_hits = 0
        self.query_cache_misses = 0

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> list[VectorSearchResult]:
        model_name = str(getattr(self.backend, "model_name", "unknown"))
        if model_name != self.index.model_name:
            raise VectorIndexConfigurationError(
                f"embedding backend model is {model_name!r}, expected {self.index.model_name!r}"
            )
        manifest = assert_index_generation_ready(self.index)
        if manifest is None:
            _warn_legacy_index_once(self.index)
        else:
            if not isinstance(self.backend, SentenceTransformerBackend):
                raise VectorIndexConfigurationError(
                    "managed index requires the verified local embedding backend"
                )
            try:
                self.backend.verify_model_integrity(required=True)
            except ModelIntegrityError as exc:
                raise VectorIndexConfigurationError(
                    "managed index local embedding model failed integrity verification"
                ) from exc
        query_vector = _cached_query_vector(model_name, query)
        if query_vector is None:
            vectors = self.backend.encode_queries([query])
            query_vector = vectors[0]
            _remember_query_vector(model_name, query, query_vector)
            self.query_cache_misses += 1
        else:
            self.query_cache_hits += 1
        references = self.index.search(
            query_vector,
            limit=limit,
            article_ids=article_ids,
            sections=sections,
            _manifest_validated=True,
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
        try:
            if self.close_backend:
                self.backend.close()
        finally:
            self.index.close()
