"""Content-addressed deep-research response cache with complete signatures."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.corpora import CorpusScope, corpus_paths
from app.deep_research.claims import _SYSTEM_PROMPT as CLAIM_PROMPT
from app.deep_research.contextual_summary import _SYSTEM_PROMPT as CONTEXTUAL_PROMPT
from app.deep_research.iteration import _ASSESSMENT_SYSTEM_PROMPT as GAP_PROMPT
from app.deep_research.verification import _SYSTEM_PROMPT as VERIFICATION_PROMPT

_CACHE_SCHEMA_VERSION = 2
_RENDERING_CONTRACT_VERSION = "sqlite-renderer-v1"


def _canonical_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _corpus_fingerprint(path: Path) -> str:
    """Fingerprint authoritative article identities without mutating a missing database."""

    if not path.is_file():
        return _canonical_hash({"state": "absent"})
    try:
        with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'articles'"
            ).fetchone()
            if table is None:
                return _canonical_hash({"state": "no_articles_table"})
            rows = connection.execute(
                """
                SELECT id, sha256, validation_status
                FROM articles
                ORDER BY id
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise RuntimeError("deep-research cache cannot fingerprint scoped SQLite") from error
    return _canonical_hash(
        {
            "state": "available",
            "articles": [list(row) for row in rows],
        }
    )


def combined_corpus_fingerprint(settings: Settings) -> str:
    """Return the canonical single hash used by signed CiderQA run contexts."""

    return _corpus_fingerprint(corpus_paths(settings, CorpusScope.COMMON).database_path)


def _local_model_manifest(settings: Settings, model_name: str) -> str | None:
    model_directory = settings.paths.models_dir / model_name.replace("/", "--")
    manifest = model_directory / "manifest.json"
    return hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None


class DeepResearchCacheSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = _CACHE_SCHEMA_VERSION
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    common_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    models_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content_address(self) -> DeepResearchCacheSignature:
        dimensions = self.model_dump(exclude={"cache_key_sha256"})
        if self.cache_key_sha256 != _canonical_hash(dimensions):
            raise ValueError("deep-research cache key does not match its signed dimensions")
        return self

    @classmethod
    def build(
        cls,
        *,
        question: str,
        common_corpus_sha256: str,
        models: dict[str, object],
        prompts: dict[str, str],
        parameters: dict[str, object],
    ) -> DeepResearchCacheSignature:
        dimensions = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "common_corpus_sha256": common_corpus_sha256,
            "models_sha256": _canonical_hash(models),
            "prompts_sha256": _canonical_hash(prompts),
            "parameters_sha256": _canonical_hash(parameters),
        }
        return cls(
            **dimensions,
            cache_key_sha256=_canonical_hash(dimensions),
        )


class DeepResearchCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = _CACHE_SCHEMA_VERSION
    signature: DeepResearchCacheSignature
    answer_markdown: str = Field(min_length=1)
    details: dict[str, object]
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_response_hash(self) -> DeepResearchCacheEntry:
        expected = _canonical_hash(
            {
                "answer_markdown": self.answer_markdown,
                "details": self.details,
            }
        )
        if self.response_sha256 != expected:
            raise ValueError("deep-research cached response hash is invalid")
        return self


class DeepResearchResponseCache:
    def __init__(self, settings: Settings, root: Path) -> None:
        self.settings = settings
        self.root = root

    def signature(self, question: str) -> DeepResearchCacheSignature:
        models = {
            "argo": self.settings.argo.model,
            "embedding": self.settings.embeddings.model_name,
            "embedding_manifest_sha256": _local_model_manifest(
                self.settings,
                self.settings.embeddings.model_name,
            ),
            "reranker": self.settings.reranker.model_name,
            "reranker_manifest_sha256": _local_model_manifest(
                self.settings,
                self.settings.reranker.model_name,
            ),
        }
        prompts = {
            "contextual": CONTEXTUAL_PROMPT,
            "gap": GAP_PROMPT,
            "claims": CLAIM_PROMPT,
            "verification": VERIFICATION_PROMPT,
            "rendering_contract": _RENDERING_CONTRACT_VERSION,
        }
        parameters = {
            "deep_research": self.settings.deep_research.model_dump(mode="json"),
            "embeddings": self.settings.embeddings.model_dump(mode="json"),
            "reranker": self.settings.reranker.model_dump(mode="json"),
            "retrieval": self.settings.retrieval.model_dump(mode="json"),
        }
        return DeepResearchCacheSignature.build(
            question=" ".join(question.split()),
            common_corpus_sha256=combined_corpus_fingerprint(self.settings),
            models=models,
            prompts=prompts,
            parameters=parameters,
        )

    def _path(self, signature: DeepResearchCacheSignature) -> Path:
        return self.root / signature.cache_key_sha256 / "entry.json"

    def get(
        self,
        signature: DeepResearchCacheSignature,
    ) -> DeepResearchCacheEntry | None:
        path = self._path(signature)
        if not path.is_file():
            return None
        entry = DeepResearchCacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
        if entry.signature != signature:
            raise RuntimeError("deep-research cache entry signature does not match")
        return entry

    def put(
        self,
        signature: DeepResearchCacheSignature,
        *,
        answer_markdown: str,
        details: dict[str, object],
    ) -> DeepResearchCacheEntry:
        response_payload = {
            "answer_markdown": answer_markdown,
            "details": details,
        }
        entry = DeepResearchCacheEntry(
            signature=signature,
            answer_markdown=answer_markdown,
            details=details,
            response_sha256=_canonical_hash(response_payload),
        )
        path = self._path(signature)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return entry
