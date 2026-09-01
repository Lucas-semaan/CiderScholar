"""Content-addressed, local cache for reproducible retrieval results.

The cache deliberately stores hashes of the query dimensions, rather than the
query or document text.  A new corpus fingerprint produces a different key;
``invalidate_stale`` is only a disk-reclamation operation, never required for
correctness.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

_SCHEMA_VERSION = 2
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

T = TypeVar("T")
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


def _canonical_hash(value: object) -> str:
    """Hash JSON data deterministically without retaining its source text."""

    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_text(value: str) -> str:
    """Normalize non-semantic whitespace only; query order and casing are preserved."""

    return " ".join(value.split())


class RetrievalCacheSignature(BaseModel):
    """All inputs that can affect a retrieval result, represented by hashes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=_SCHEMA_VERSION, ge=1)
    query_sha256: str = Field(pattern=_SHA256_PATTERN)
    variants_sha256: str = Field(pattern=_SHA256_PATTERN)
    corpus_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    scope: str = Field(min_length=1, max_length=128)
    retrieval_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    embedding_sha256: str = Field(pattern=_SHA256_PATTERN)
    reranker_sha256: str = Field(pattern=_SHA256_PATTERN)
    filters_limits_sha256: str = Field(pattern=_SHA256_PATTERN)
    cache_key_sha256: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def build(
        cls,
        *,
        query: str,
        variants: list[str] | tuple[str, ...],
        corpus_fingerprint: str,
        scope: str,
        retrieval_config: dict[str, JsonValue],
        embedding: dict[str, JsonValue],
        reranker: dict[str, JsonValue],
        filters_limits: dict[str, JsonValue],
        schema_version: int = _SCHEMA_VERSION,
    ) -> RetrievalCacheSignature:
        """Build a key from normalized query inputs and complete retrieval context.

        ``embedding`` and ``reranker`` should contain their model name and
        immutable manifest hash.  Callers own corpus fingerprint construction.
        """

        dimensions = {
            "schema_version": schema_version,
            "query_sha256": _canonical_hash(_normalized_text(query)),
            "variants_sha256": _canonical_hash([_normalized_text(item) for item in variants]),
            "corpus_fingerprint": corpus_fingerprint,
            "scope": scope,
            "retrieval_config_sha256": _canonical_hash(retrieval_config),
            "embedding_sha256": _canonical_hash(embedding),
            "reranker_sha256": _canonical_hash(reranker),
            "filters_limits_sha256": _canonical_hash(filters_limits),
        }
        return cls(**dimensions, cache_key_sha256=_canonical_hash(dimensions))

    def validates_content_address(self) -> bool:
        dimensions = self.model_dump(exclude={"cache_key_sha256"})
        return self.cache_key_sha256 == _canonical_hash(dimensions)


class RetrievalCacheEntry(BaseModel):
    """A JSON-safe result plus integrity metadata, without PDF content in its key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=_SCHEMA_VERSION, ge=1)
    signature: RetrievalCacheSignature
    result: JsonValue
    result_sha256: str = Field(pattern=_SHA256_PATTERN)

    def validates_integrity(self) -> bool:
        return (
            self.schema_version == _SCHEMA_VERSION
            and self.signature.schema_version == _SCHEMA_VERSION
            and self.signature.validates_content_address()
            and self.result_sha256 == _canonical_hash(self.result)
        )


class RetrievalResultCache:
    """Filesystem cache whose reads turn corruption into a safe cache miss."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._closed = False

    def close(self) -> None:
        """Explicit lifecycle boundary; this cache holds no open file handles."""

        self._closed = True

    def __enter__(self) -> RetrievalResultCache:
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("retrieval result cache is closed")

    def _path(self, signature: RetrievalCacheSignature) -> Path:
        return self.root / signature.cache_key_sha256 / "entry.json"

    def get(self, signature: RetrievalCacheSignature) -> JsonValue | None:
        """Return a valid JSON result, or ``None`` for absent, corrupt, or stale data."""

        self._require_open()
        if not signature.validates_content_address():
            return None
        path = self._path(signature)
        try:
            entry = RetrievalCacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
            return None
        if not entry.validates_integrity() or entry.signature != signature:
            return None
        return entry.result

    def get_typed(self, signature: RetrievalCacheSignature, result_type: type[T]) -> T | None:
        """Read a cache hit and validate it against a caller-owned Pydantic type."""

        result = self.get(signature)
        if result is None:
            return None
        try:
            return TypeAdapter(result_type).validate_python(result)
        except ValidationError:
            return None

    def put(self, signature: RetrievalCacheSignature, result: object) -> RetrievalCacheEntry:
        """Atomically persist a JSON-valid result under its content-addressed key."""

        self._require_open()
        if signature.schema_version != _SCHEMA_VERSION:
            raise ValueError("retrieval cache signature schema version is stale")
        if not signature.validates_content_address():
            raise ValueError("retrieval cache signature is not content-addressed")
        validated_result = _JSON_VALUE_ADAPTER.validate_python(result)
        entry = RetrievalCacheEntry(
            signature=signature,
            result=validated_result,
            result_sha256=_canonical_hash(validated_result),
        )
        path = self._path(signature)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(entry.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return entry

    def invalidate_stale(self, active_corpus_fingerprint: str) -> int:
        """Remove valid entries for other corpus fingerprints and corrupt entries.

        The active fingerprint is validated so an accidental broad cleanup cannot
        target the cache.  Correct retrieval does not depend on calling this.
        """

        self._require_open()
        if not __import__("re").fullmatch(_SHA256_PATTERN, active_corpus_fingerprint):
            raise ValueError("active corpus fingerprint must be a SHA-256 digest")
        removed = 0
        if not self.root.is_dir():
            return removed
        for path in self.root.glob("*/entry.json"):
            try:
                entry = RetrievalCacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
                is_stale = (
                    not entry.validates_integrity()
                    or entry.signature.corpus_fingerprint != active_corpus_fingerprint
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
                is_stale = True
            if is_stale:
                path.unlink(missing_ok=True)
                with suppress(OSError):
                    path.parent.rmdir()
                removed += 1
        return removed
