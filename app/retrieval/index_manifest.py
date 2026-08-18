"""Versioned, text-free manifests for local scientific chunk-index generations."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from qdrant_client import models

from app.config import Settings
from app.database.sqlite import Database
from app.desktop.model_integrity import MODEL_MANIFEST, ModelIntegrityError
from app.ingestion.embeddings import local_model_path, verify_local_embedding_model

INDEX_MANIFEST_DIRECTORY = "index-generation"
CHUNKER_CONTRACT_VERSION = 2
_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class IndexGenerationManifestError(RuntimeError):
    """Raised when a local index generation cannot be used safely."""


class IndexGenerationCompatibilityError(IndexGenerationManifestError):
    """Raised before a semantic configuration is mixed into an existing generation."""


class IndexGenerationIntegrityError(IndexGenerationManifestError):
    """Raised when Qdrant and SQLite do not describe the same indexed snapshot."""


class _QdrantIndex(Protocol):
    settings: Settings
    path: Path
    collection_name: str
    model_name: str

    @property
    def client(self): ...

    def collection_exists(self) -> bool: ...

    def count(self) -> int: ...


class IndexGenerationManifest(BaseModel):
    """Provenance and compatibility information for one `science_chunks` generation."""

    model_config = ConfigDict(extra="forbid")

    format_version: Literal[1] = 1
    generation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    state: Literal["building", "ready"]
    created_at: datetime
    updated_at: datetime
    collection_name: str = Field(min_length=1, max_length=63)
    semantic_signature_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding_model_name: str = Field(min_length=1, max_length=300)
    embedding_model_manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    # A building generation is written before a fresh collection has received its
    # first vector, so its dimension is intentionally unknown (0). A ready
    # generation must always record the concrete Qdrant dimension.
    embedding_dimension: int = Field(ge=0)
    embeddings_normalized: bool
    embedding_max_sequence_length: int = Field(ge=1)
    embedding_query_prefix: str
    embedding_passage_prefix: str
    embedding_device: str = Field(min_length=1, max_length=20)
    chunker_contract_version: int = Field(ge=1)
    chunk_target_tokens: int = Field(ge=1)
    chunk_max_tokens: int = Field(ge=1)
    chunk_overlap_tokens: int = Field(ge=0)
    qdrant_distance: Literal["cosine"] = "cosine"
    qdrant_on_disk_vectors: bool
    qdrant_on_disk_payload: bool
    qdrant_effective_on_disk_vectors: bool | None
    qdrant_effective_on_disk_payload: bool | None
    database_schema_version: int = Field(ge=0)
    indexed_article_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    indexed_chunk_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    indexed_article_count: int = Field(ge=0)
    indexed_chunk_count: int = Field(ge=0)
    total_chunk_count: int = Field(ge=0)
    embedding_status_counts: dict[str, int]
    qdrant_point_count: int = Field(ge=0)
    fully_indexed: bool

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("index generation timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def coherent_generation(self) -> IndexGenerationManifest:
        if self.updated_at < self.created_at:
            raise ValueError("index generation update precedes creation")
        if not _COLLECTION_NAME.fullmatch(self.collection_name):
            raise ValueError("index generation collection name is invalid")
        if self.indexed_chunk_count > self.total_chunk_count:
            raise ValueError("indexed chunks cannot exceed total chunks")
        if self.state == "ready" and self.embedding_dimension <= 0:
            raise ValueError("ready index generation must record a vector dimension")
        if self.state == "ready" and self.embedding_model_manifest_sha256 is None:
            raise ValueError("ready index generation must record a model manifest hash")
        if self.state == "ready" and self.qdrant_point_count != self.indexed_chunk_count:
            raise ValueError("Qdrant point count must equal SQLite indexed chunks")
        return self

    def semantic_values(self) -> dict[str, object]:
        """Values which make vectors/chunks mutually incompatible if they change."""

        return {
            "collection_name": self.collection_name,
            "embedding_model_name": self.embedding_model_name,
            "embedding_model_manifest_sha256": self.embedding_model_manifest_sha256,
            "embeddings_normalized": self.embeddings_normalized,
            "embedding_max_sequence_length": self.embedding_max_sequence_length,
            "embedding_query_prefix": self.embedding_query_prefix,
            "embedding_passage_prefix": self.embedding_passage_prefix,
            "embedding_device": self.embedding_device,
            "chunker_contract_version": self.chunker_contract_version,
            "chunk_target_tokens": self.chunk_target_tokens,
            "chunk_max_tokens": self.chunk_max_tokens,
            "chunk_overlap_tokens": self.chunk_overlap_tokens,
            "qdrant_distance": self.qdrant_distance,
            "qdrant_on_disk_vectors": self.qdrant_on_disk_vectors,
            "qdrant_on_disk_payload": self.qdrant_on_disk_payload,
        }

    def signature_values(self) -> dict[str, object]:
        """Values covered by the sidecar checksum, including Qdrant dimension."""

        return {
            **self.semantic_values(),
            "embedding_dimension": self.embedding_dimension,
            "qdrant_effective_on_disk_vectors": self.qdrant_effective_on_disk_vectors,
            "qdrant_effective_on_disk_payload": self.qdrant_effective_on_disk_payload,
        }


def index_generation_manifest_path(
    settings: Settings,
    *,
    qdrant_path: Path | None = None,
    collection_name: str | None = None,
) -> Path:
    """Return the collection-specific sidecar path inside Qdrant's packaged root."""

    root = qdrant_path.resolve() if qdrant_path is not None else settings.paths.qdrant_dir.resolve()
    collection = collection_name or settings.qdrant.collection_name
    if not _COLLECTION_NAME.fullmatch(collection):
        raise ValueError("invalid Qdrant collection name")
    return root / INDEX_MANIFEST_DIRECTORY / f"{collection}.json"


def _canonical_sha256(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@lru_cache(maxsize=32)
def _cached_model_manifest_sha256(path: str, modified_ns: int, size_bytes: int) -> str:
    """Hash a stable model-manifest revision once, never once per search."""

    del modified_ns, size_bytes
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _model_manifest_sha256(settings: Settings, model_name: str) -> str | None:
    manifest_path = local_model_path(settings, model_name).resolve() / MODEL_MANIFEST
    try:
        stat = manifest_path.stat()
    except OSError:
        return None
    if not manifest_path.is_file():
        return None
    return _cached_model_manifest_sha256(
        str(manifest_path),
        stat.st_mtime_ns,
        stat.st_size,
    )


def _expected_semantic_values(
    settings: Settings,
    model_name: str,
    *,
    collection_name: str | None = None,
) -> dict[str, object]:
    return {
        "collection_name": collection_name or settings.qdrant.collection_name,
        "embedding_model_name": model_name,
        "embedding_model_manifest_sha256": _model_manifest_sha256(settings, model_name),
        "embeddings_normalized": settings.embeddings.normalize,
        "embedding_max_sequence_length": settings.embeddings.max_sequence_length,
        "embedding_query_prefix": settings.embeddings.query_prefix,
        "embedding_passage_prefix": settings.embeddings.passage_prefix,
        "embedding_device": settings.embeddings.device,
        "chunker_contract_version": CHUNKER_CONTRACT_VERSION,
        "chunk_target_tokens": settings.ingestion.target_tokens,
        "chunk_max_tokens": settings.ingestion.max_tokens,
        "chunk_overlap_tokens": settings.ingestion.overlap_tokens,
        "qdrant_distance": settings.qdrant.distance,
        "qdrant_on_disk_vectors": settings.qdrant.on_disk_vectors,
        "qdrant_on_disk_payload": settings.qdrant.on_disk_payload,
    }


def _require_verified_local_model(index: _QdrantIndex) -> None:
    """Hash-verify the local model before a managed generation becomes ready.

    The expensive file hashing is cached by the embedding module for an unchanged
    file-metadata revision. It is deliberately not called from sidecar-only
    compatibility checks, because actual query loading verifies the model there.
    """

    try:
        verify_local_embedding_model(
            local_model_path(index.settings, index.model_name),
            index.model_name,
            required=True,
        )
    except ModelIntegrityError as exc:
        raise IndexGenerationIntegrityError(
            "managed index requires a verified local embedding model"
        ) from exc


def _effective_bool(value: object) -> bool | None:
    """Keep Qdrant's nullable storage flags distinct from a configured false."""

    return None if value is None else bool(value)


def _indexed_snapshots(database: Database) -> tuple[int, str, str, int, int, dict[str, int]]:
    """Hash only indexed chunks, so a pending ingestion does not invalidate a ready index."""

    with closing(database.connect()) as connection:
        connection.execute("BEGIN")
        try:
            schema_row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            if schema_row is None or schema_row[0] is None:
                raise IndexGenerationIntegrityError("SQLite schema version is unavailable")
            schema_version = int(schema_row[0])
            article_digest = hashlib.sha256()
            article_count = 0
            for row in connection.execute(
                """
                SELECT DISTINCT articles.id, articles.sha256
                FROM articles
                INNER JOIN chunks ON chunks.article_id = articles.id
                WHERE chunks.embedding_status = 'indexed'
                ORDER BY articles.id
                """
            ):
                article_digest.update(
                    json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode(
                        "utf-8"
                    )
                )
                article_digest.update(b"\n")
                article_count += 1
            chunk_digest = hashlib.sha256()
            indexed_chunk_count = 0
            for row in connection.execute(
                """
                SELECT id, article_id, chunk_index, section, subsection, page_start, page_end,
                       token_count, text
                FROM chunks
                WHERE embedding_status = 'indexed'
                ORDER BY id
                """
            ):
                values = tuple(row)
                chunk_digest.update(
                    json.dumps(values[:-1], ensure_ascii=False, separators=(",", ":")).encode(
                        "utf-8"
                    )
                )
                chunk_digest.update(hashlib.sha256(str(values[-1]).encode("utf-8")).digest())
                chunk_digest.update(b"\n")
                indexed_chunk_count += 1
            status_counts = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT embedding_status, COUNT(*) FROM chunks GROUP BY embedding_status"
                )
            }
        finally:
            connection.rollback()
    return (
        schema_version,
        article_digest.hexdigest(),
        chunk_digest.hexdigest(),
        article_count,
        indexed_chunk_count,
        status_counts,
    )


def load_index_generation_manifest(path: str | Path) -> IndexGenerationManifest:
    source = Path(path)
    try:
        return IndexGenerationManifest.model_validate_json(source.read_bytes())
    except (OSError, ValueError) as exc:
        raise IndexGenerationManifestError(
            "index generation manifest is missing or invalid"
        ) from exc


def _assert_semantic_compatibility(
    index: _QdrantIndex,
    manifest: IndexGenerationManifest,
) -> None:
    expected = _expected_semantic_values(
        index.settings,
        index.model_name,
        collection_name=index.collection_name,
    )
    actual = manifest.semantic_values()
    mismatches = [name for name, value in expected.items() if actual.get(name) != value]
    if mismatches:
        raise IndexGenerationCompatibilityError(
            "index generation is incompatible with current settings: " + ", ".join(mismatches)
        )
    if manifest.semantic_signature_sha256 != _canonical_sha256(manifest.signature_values()):
        raise IndexGenerationIntegrityError("index generation semantic signature is invalid")


def _assert_qdrant_collection_matches_manifest(
    manifest: IndexGenerationManifest,
    collection_info: Any,
) -> None:
    """Validate the physical collection fields captured by a ready sidecar."""

    vectors = collection_info.config.params.vectors
    metadata = collection_info.config.metadata or {}
    if not isinstance(vectors, models.VectorParams):
        raise IndexGenerationIntegrityError("index generation does not support named vectors")
    if int(vectors.size) != manifest.embedding_dimension:
        raise IndexGenerationIntegrityError("Qdrant dimension differs from the ready manifest")
    if vectors.distance != models.Distance.COSINE:
        raise IndexGenerationIntegrityError("Qdrant distance differs from the ready manifest")
    if _effective_bool(vectors.on_disk) != manifest.qdrant_effective_on_disk_vectors:
        raise IndexGenerationIntegrityError("Qdrant vector storage differs from the ready manifest")
    if (
        _effective_bool(collection_info.config.params.on_disk_payload)
        != manifest.qdrant_effective_on_disk_payload
    ):
        raise IndexGenerationIntegrityError(
            "Qdrant payload storage differs from the ready manifest"
        )
    if metadata.get("model_name") != manifest.embedding_model_name:
        raise IndexGenerationIntegrityError("Qdrant model differs from the ready manifest")
    if metadata.get("vector_dimension") != manifest.embedding_dimension:
        raise IndexGenerationIntegrityError(
            "Qdrant metadata dimension differs from the ready manifest"
        )


def validate_packaged_index_generation(
    path: str | Path,
    *,
    collection_name: str,
    qdrant_point_count: int,
    indexed_chunk_count: int,
    collection_info: Any | None,
) -> IndexGenerationManifest:
    """Validate an optional packaged sidecar without requiring local model files.

    Legacy packages have no sidecar and are accepted by their caller. Once a
    sidecar is included, the package must carry a ready collection whose local
    metadata and indexed counts agree with its self-checking contract.
    """

    manifest = load_index_generation_manifest(path)
    if manifest.state != "ready":
        raise IndexGenerationIntegrityError("packaged index generation is not ready")
    if manifest.embedding_model_manifest_sha256 is None:
        raise IndexGenerationIntegrityError(
            "packaged ready generation has no embedding model manifest hash"
        )
    if manifest.collection_name != collection_name:
        raise IndexGenerationIntegrityError("packaged index generation collection is incompatible")
    if manifest.semantic_signature_sha256 != _canonical_sha256(manifest.signature_values()):
        raise IndexGenerationIntegrityError("packaged index generation signature is invalid")
    if manifest.qdrant_point_count != qdrant_point_count:
        raise IndexGenerationIntegrityError("packaged Qdrant count differs from its generation")
    if manifest.indexed_chunk_count != indexed_chunk_count:
        raise IndexGenerationIntegrityError("packaged SQLite count differs from its generation")
    if collection_info is None:
        raise IndexGenerationIntegrityError("packaged ready generation has no Qdrant collection")
    _assert_qdrant_collection_matches_manifest(manifest, collection_info)
    return manifest


def assert_index_generation_ready(index: _QdrantIndex) -> IndexGenerationManifest | None:
    """Validate sidecar semantics cheaply before a vector read or write.

    Missing sidecars deliberately remain readable as legacy generations. Once a sidecar exists,
    malformed, incompatible, or in-progress generations fail closed instead of serving mixed data.
    """

    # The index owns the existing corpus-level file lock. Acquire it before
    # observing the sidecar so a concurrent writer cannot switch ready/building
    # between this check and the ensuing vector read.
    _ = index.client
    source = index_generation_manifest_path(
        index.settings,
        qdrant_path=index.path,
        collection_name=index.collection_name,
    )
    if not source.exists():
        return None
    manifest = load_index_generation_manifest(source)
    _assert_semantic_compatibility(index, manifest)
    if manifest.state != "ready":
        raise IndexGenerationIntegrityError("index generation is not ready")
    if manifest.embedding_model_manifest_sha256 is None:
        raise IndexGenerationIntegrityError(
            "ready index generation has no embedding model manifest hash"
        )
    if not index.collection_exists():
        raise IndexGenerationIntegrityError("ready index generation has no Qdrant collection")
    info = index.client.get_collection(index.collection_name)
    _assert_qdrant_collection_matches_manifest(manifest, info)
    return manifest


def assert_index_generation_mutable(index: _QdrantIndex) -> IndexGenerationManifest | None:
    """Allow a compatible in-progress generation to resume writes, never reads."""

    _ = index.client
    source = index_generation_manifest_path(
        index.settings,
        qdrant_path=index.path,
        collection_name=index.collection_name,
    )
    if not source.exists():
        return None
    manifest = load_index_generation_manifest(source)
    _assert_semantic_compatibility(index, manifest)
    if manifest.state != "building":
        raise IndexGenerationIntegrityError("index generation is not marked building")
    return manifest


def resume_index_generation(index: _QdrantIndex) -> IndexGenerationManifest | None:
    """Return a compatible interrupted generation, without changing a ready one."""

    _ = index.client
    source = index_generation_manifest_path(
        index.settings,
        qdrant_path=index.path,
        collection_name=index.collection_name,
    )
    if not source.exists():
        return None
    manifest = load_index_generation_manifest(source)
    _assert_semantic_compatibility(index, manifest)
    return manifest if manifest.state == "building" else None


def prepare_index_generation_mutation(index: _QdrantIndex) -> IndexGenerationManifest | None:
    """Transition ready generations to building or safely resume a compatible one."""

    _ = index.client
    source = index_generation_manifest_path(
        index.settings,
        qdrant_path=index.path,
        collection_name=index.collection_name,
    )
    if not source.exists():
        return None
    manifest = load_index_generation_manifest(source)
    _assert_semantic_compatibility(index, manifest)
    if manifest.state == "building":
        return manifest
    assert_index_generation_ready(index)
    return mark_index_generation_building(index)


def mark_index_generation_building(index: _QdrantIndex) -> IndexGenerationManifest | None:
    """Atomically mark an existing ready generation unavailable before mutation."""

    _ = index.client
    source = index_generation_manifest_path(
        index.settings,
        qdrant_path=index.path,
        collection_name=index.collection_name,
    )
    if not source.exists():
        return None
    manifest = load_index_generation_manifest(source)
    updated = manifest.model_copy(update={"state": "building", "updated_at": datetime.now(UTC)})
    _write_manifest(source, updated)
    return updated


def begin_index_generation(index: _QdrantIndex) -> IndexGenerationManifest:
    """Persist a new fail-closed generation before a destructive full rebuild.

    This deliberately does not inherit the previous snapshot: an interrupted
    ``--recreate`` must never look like a usable prior generation. The zero
    dimension is valid only while ``state == 'building'`` and becomes concrete
    when the successful rebuild writes the ready manifest.
    """

    _ = index.client
    _require_verified_local_model(index)
    expected = _expected_semantic_values(
        index.settings,
        index.model_name,
        collection_name=index.collection_name,
    )
    now = datetime.now(UTC)
    manifest = IndexGenerationManifest(
        generation_id=str(uuid.uuid4()),
        state="building",
        created_at=now,
        updated_at=now,
        embedding_dimension=0,
        semantic_signature_sha256=_canonical_sha256(
            {
                **expected,
                "embedding_dimension": 0,
                "qdrant_effective_on_disk_vectors": None,
                "qdrant_effective_on_disk_payload": None,
            }
        ),
        database_schema_version=0,
        indexed_article_fingerprint_sha256="0" * 64,
        indexed_chunk_fingerprint_sha256="0" * 64,
        indexed_article_count=0,
        indexed_chunk_count=0,
        total_chunk_count=0,
        embedding_status_counts={},
        qdrant_point_count=0,
        fully_indexed=False,
        qdrant_effective_on_disk_vectors=None,
        qdrant_effective_on_disk_payload=None,
        **expected,
    )
    _write_manifest(
        index_generation_manifest_path(
            index.settings,
            qdrant_path=index.path,
            collection_name=index.collection_name,
        ),
        manifest,
    )
    return manifest


def build_ready_index_generation_manifest(
    database: Database,
    index: _QdrantIndex,
    *,
    generation_id: str | None = None,
    created_at: datetime | None = None,
) -> IndexGenerationManifest:
    """Create a ready sidecar after checking point counts and indexed SQLite content."""

    _require_verified_local_model(index)
    if not index.collection_exists():
        raise IndexGenerationIntegrityError("cannot manifest a missing Qdrant collection")
    info = index.client.get_collection(index.collection_name)
    vectors = info.config.params.vectors
    metadata = info.config.metadata or {}
    if not isinstance(vectors, models.VectorParams):
        raise IndexGenerationIntegrityError("index generation does not support named vectors")
    if vectors.distance != models.Distance.COSINE:
        raise IndexGenerationIntegrityError("index generation must use cosine distance")
    if metadata.get("model_name") != index.model_name:
        raise IndexGenerationIntegrityError("Qdrant metadata model does not match the index")
    (
        schema_version,
        article_fingerprint,
        chunk_fingerprint,
        article_count,
        indexed_chunk_count,
        status_counts,
    ) = _indexed_snapshots(database)
    point_count = index.count()
    if point_count != indexed_chunk_count:
        raise IndexGenerationIntegrityError(
            "Qdrant point count does not match SQLite indexed chunks "
            f"({point_count} != {indexed_chunk_count})"
        )
    expected = _expected_semantic_values(
        index.settings,
        index.model_name,
        collection_name=index.collection_name,
    )
    now = datetime.now(UTC)
    total_chunk_count = sum(status_counts.values())
    return IndexGenerationManifest(
        generation_id=generation_id or str(uuid.uuid4()),
        state="ready",
        created_at=(created_at or now).astimezone(UTC),
        updated_at=now,
        embedding_dimension=int(vectors.size),
        semantic_signature_sha256=_canonical_sha256(
            {
                **expected,
                "embedding_dimension": int(vectors.size),
                "qdrant_effective_on_disk_vectors": _effective_bool(vectors.on_disk),
                "qdrant_effective_on_disk_payload": _effective_bool(
                    info.config.params.on_disk_payload
                ),
            }
        ),
        database_schema_version=schema_version,
        indexed_article_fingerprint_sha256=article_fingerprint,
        indexed_chunk_fingerprint_sha256=chunk_fingerprint,
        indexed_article_count=article_count,
        indexed_chunk_count=indexed_chunk_count,
        total_chunk_count=total_chunk_count,
        embedding_status_counts=status_counts,
        qdrant_point_count=point_count,
        fully_indexed=(
            total_chunk_count == indexed_chunk_count
            and not any(
                status_counts.get(status, 0) for status in ("pending", "processing", "failed")
            )
        ),
        qdrant_effective_on_disk_vectors=_effective_bool(vectors.on_disk),
        qdrant_effective_on_disk_payload=_effective_bool(info.config.params.on_disk_payload),
        **expected,
    )


def _write_manifest(destination: Path, manifest: IndexGenerationManifest) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        temporary.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_ready_index_generation_manifest(
    database: Database,
    index: _QdrantIndex,
    *,
    generation_id: str | None = None,
    created_at: datetime | None = None,
) -> IndexGenerationManifest:
    """Persist a complete ready manifest after a successful indexed-state transition."""

    manifest = build_ready_index_generation_manifest(
        database,
        index,
        generation_id=generation_id,
        created_at=created_at,
    )
    _assert_exact_indexed_point_mapping(database, index)
    _write_manifest(
        index_generation_manifest_path(
            index.settings,
            qdrant_path=index.path,
            collection_name=index.collection_name,
        ),
        manifest,
    )
    return manifest


def verify_index_generation_snapshot(
    database: Database,
    index: _QdrantIndex,
) -> IndexGenerationManifest:
    """Recompute the explicit snapshot; never use this expensive verification on query paths."""

    manifest = assert_index_generation_ready(index)
    if manifest is None:
        raise IndexGenerationManifestError("legacy index generation has no manifest")
    current = build_ready_index_generation_manifest(
        database,
        index,
        generation_id=manifest.generation_id,
        created_at=manifest.created_at,
    )
    immutable_fields = (
        "collection_name",
        "semantic_signature_sha256",
        "embedding_model_name",
        "embedding_model_manifest_sha256",
        "embedding_dimension",
        "embeddings_normalized",
        "embedding_max_sequence_length",
        "embedding_query_prefix",
        "embedding_passage_prefix",
        "embedding_device",
        "chunker_contract_version",
        "chunk_target_tokens",
        "chunk_max_tokens",
        "chunk_overlap_tokens",
        "qdrant_distance",
        "qdrant_on_disk_vectors",
        "qdrant_on_disk_payload",
        "database_schema_version",
        "indexed_article_fingerprint_sha256",
        "indexed_chunk_fingerprint_sha256",
        "indexed_article_count",
        "indexed_chunk_count",
        "qdrant_point_count",
    )
    if any(getattr(current, field) != getattr(manifest, field) for field in immutable_fields):
        raise IndexGenerationIntegrityError(
            "index generation snapshot no longer matches SQLite/Qdrant"
        )
    _assert_exact_indexed_point_mapping(database, index)
    return manifest


def _assert_exact_indexed_point_mapping(database: Database, index: _QdrantIndex) -> None:
    """Ensure point IDs and non-text retrieval payloads mirror indexed SQLite rows."""

    expected = _indexed_chunk_payloads(database)
    actual = _qdrant_chunk_payloads(index)
    _assert_exact_chunk_payload_mapping(expected, actual, model_name=index.model_name)


def assert_packaged_index_generation_mapping(
    connection: Any,
    client: Any,
    *,
    collection_name: str,
    model_name: str,
) -> None:
    """Verify every staged SQLite chunk against its Qdrant routing payload.

    This runs only while installing a package that already supplied a managed
    generation sidecar. It intentionally excludes vectors and article text.
    """

    expected = _indexed_chunk_payloads_from_connection(connection)
    actual = _qdrant_chunk_payloads_from_client(client, collection_name)
    _assert_exact_chunk_payload_mapping(expected, actual, model_name=model_name)


def _assert_exact_chunk_payload_mapping(
    expected: dict[int, tuple[str, str | None, int, int]],
    actual: dict[int, dict[str, object]],
    *,
    model_name: str,
) -> None:
    """Compare IDs and retrieval-filter metadata without exposing source text."""

    if set(actual) != set(expected):
        raise IndexGenerationIntegrityError(
            "index generation point ids do not match SQLite indexed chunks"
        )
    for chunk_id, (article_id, section, page_start, page_end) in expected.items():
        payload = actual[chunk_id]
        if (
            payload.get("kind") != "chunk"
            or payload.get("chunk_id") != chunk_id
            or payload.get("article_id") != article_id
            or payload.get("section") != section
            or payload.get("page_start") != page_start
            or payload.get("page_end") != page_end
            or payload.get("model_name") != model_name
        ):
            raise IndexGenerationIntegrityError(
                "Qdrant point payload does not match its SQLite chunk"
            )


def _indexed_chunk_payloads(database: Database) -> dict[int, tuple[str, str | None, int, int]]:
    """Return retrieval metadata only; scientific text never enters the sidecar path."""

    with closing(database.connect()) as connection:
        return _indexed_chunk_payloads_from_connection(connection)


def _indexed_chunk_payloads_from_connection(
    connection: Any,
) -> dict[int, tuple[str, str | None, int, int]]:
    """Return indexed chunk routing fields from an already-open SQLite connection."""

    return {
        int(row[0]): (str(row[1]), row[2], int(row[3]), int(row[4]))
        for row in connection.execute(
            """
            SELECT id, article_id, section, page_start, page_end
            FROM chunks
            WHERE embedding_status = 'indexed'
            """
        )
    }


def _qdrant_chunk_payloads(index: _QdrantIndex) -> dict[int, dict[str, object]]:
    """Enumerate point IDs and small routing payloads without loading vectors."""

    return _qdrant_chunk_payloads_from_client(index.client, index.collection_name)


def _qdrant_chunk_payloads_from_client(
    client: Any,
    collection_name: str,
) -> dict[int, dict[str, object]]:
    """Enumerate point IDs and payloads from an arbitrary local Qdrant client."""

    payloads: dict[int, dict[str, object]] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            if isinstance(point.id, bool) or not isinstance(point.id, int):
                raise IndexGenerationIntegrityError("Qdrant point id is not a SQLite chunk id")
            if point.id in payloads or not isinstance(point.payload, dict):
                raise IndexGenerationIntegrityError("Qdrant point payload is invalid")
            payloads[point.id] = dict(point.payload)
        if offset is None:
            return payloads
