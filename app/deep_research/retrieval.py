"""Scoped full-text retrieval stage for durable deep-research jobs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.corpora import CorpusScope
from app.deep_research.models import ContextualEvidenceGate, ContextualSummaryResult
from app.deep_research.query_variants import (
    QueryVariant,
    build_bilingual_variants,
    query_variant_weight,
    variant_matches_text,
)
from app.jobs.contracts import DeepResearchPayload
from app.retrieval.multi_corpus import (
    MultiCorpusLexicalResponse,
    MultiCorpusVectorResponse,
)
from app.retrieval.reranker import MultilingualReranker, RerankerCandidate


class LexicalBackend(Protocol):
    def search(
        self,
        query: str,
        *,
        limit_per_scope: int | None = None,
        scopes: tuple[CorpusScope, ...],
    ) -> MultiCorpusLexicalResponse: ...


class VectorBackend(Protocol):
    def search(
        self,
        query: str,
        *,
        limit_per_scope: int | None = None,
        scopes: tuple[CorpusScope, ...],
    ) -> MultiCorpusVectorResponse: ...


FragmentIdentity = tuple[CorpusScope, str, int]


class FragmentTextLoader(Protocol):
    def load(self, hits: list[DeepResearchFragmentHit]) -> dict[FragmentIdentity, str]: ...


class DeepResearchFragmentHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["lexical", "vector", "rrf"]
    rank: int = Field(default=1, ge=1)
    scope: CorpusScope
    article_id: str
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    score: float
    rrf_score: float = Field(default=0.0, ge=0.0)
    rerank_score: float | None = None
    source_ranks: dict[str, int] = Field(default_factory=dict)
    source_contributions: dict[str, float] = Field(default_factory=dict)
    matched_queries: list[str] = Field(default_factory=list)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeepResearchSearchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    iteration: int = Field(default=1, ge=1, le=2)
    query: str
    explicit_gap_id: str | None = Field(default=None, pattern=r"^gap-[0-9a-f]{16}$")
    explicit_gap_description: str | None = Field(default=None, max_length=1_000)
    scopes: list[CorpusScope]
    variants: list[QueryVariant] = Field(default_factory=list)
    hits: list[DeepResearchFragmentHit]
    raw_hit_count: int = Field(default=0, ge=0)
    fused_candidate_count: int = Field(default=0, ge=0)
    rrf_candidate_count: int = Field(default=0, ge=0)
    cross_encoder_candidate_count: int = Field(default=0, ge=0)
    rrf_k: int = Field(default=60, ge=1)
    reranker_enabled: bool = False
    contextual_summary_attempted: bool = False
    contextual_summaries: list[ContextualSummaryResult] = Field(default_factory=list)
    contextual_evidence: ContextualEvidenceGate | None = None


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _FusionCandidate:
    scope: CorpusScope
    article_id: str
    chunk_id: int
    page_start: int
    page_end: int
    text: str
    text_sha256: str
    source_ranks: dict[str, int] = field(default_factory=dict)
    source_weights: dict[str, float] = field(default_factory=dict)
    matched_queries: list[str] = field(default_factory=list)

    def add_source(self, source: str, rank: int, query: str, weight: float) -> None:
        current = self.source_ranks.get(source)
        if current is None or rank < current:
            self.source_ranks[source] = rank
            self.source_weights[source] = weight
        if query not in self.matched_queries:
            self.matched_queries.append(query)


class DeepResearchRetrievalStage:
    def __init__(
        self,
        lexical: LexicalBackend,
        vector: VectorBackend,
        checkpoint_root: Path,
        *,
        limit_per_scope: int = 40,
        reranker: MultilingualReranker | None = None,
        rrf_k: int = 60,
        rrf_candidate_limit: int = 80,
        cross_encoder_candidate_limit: int = 40,
        retained_fragment_limit: int = 12,
        text_loader: FragmentTextLoader | None = None,
    ) -> None:
        if not 1 <= limit_per_scope <= 40:
            raise ValueError("deep-research retrieval limit must be between 1 and 40 per scope")
        if rrf_k < 1:
            raise ValueError("RRF k must be positive")
        if not 1 <= retained_fragment_limit <= cross_encoder_candidate_limit:
            raise ValueError("invalid retained fragment limit")
        if not cross_encoder_candidate_limit <= rrf_candidate_limit <= 80:
            raise ValueError("invalid deep-research cascade limits")
        self.lexical = lexical
        self.vector = vector
        self.checkpoint_root = checkpoint_root
        self.limit_per_scope = limit_per_scope
        self.reranker = reranker
        self.rrf_k = rrf_k
        self.rrf_candidate_limit = rrf_candidate_limit
        self.cross_encoder_candidate_limit = cross_encoder_candidate_limit
        self.retained_fragment_limit = retained_fragment_limit
        self.text_loader = text_loader
        self._retained_texts: dict[
            tuple[str, str, int],
            dict[FragmentIdentity, str],
        ] = {}

    def _path(self, payload: DeepResearchPayload, *, iteration: int = 1) -> Path:
        if iteration not in {1, 2}:
            raise ValueError("deep-research retrieval iteration must be 1 or 2")
        filename = "retrieval.json" if iteration == 1 else "retrieval-2.json"
        return (
            self.checkpoint_root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / filename
        )

    def load(
        self,
        payload: DeepResearchPayload,
        *,
        iteration: int = 1,
    ) -> DeepResearchSearchSnapshot:
        """Reload the scoped, text-free search result after a process restart."""

        path = self._path(payload, iteration=iteration)
        if not path.is_file():
            raise RuntimeError("deep-research retrieval checkpoint is missing")
        return DeepResearchSearchSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def exists(self, payload: DeepResearchPayload, *, iteration: int = 1) -> bool:
        return self._path(payload, iteration=iteration).is_file()

    @staticmethod
    def _run_key(payload: DeepResearchPayload, iteration: int) -> tuple[str, str, int]:
        return (str(payload.conversation_id), str(payload.client_request_id), iteration)

    @staticmethod
    def _identity(hit: DeepResearchFragmentHit) -> FragmentIdentity:
        return (hit.scope, hit.article_id, hit.chunk_id)

    def _save(
        self,
        payload: DeepResearchPayload,
        snapshot: DeepResearchSearchSnapshot,
    ) -> None:
        path = self._path(payload, iteration=snapshot.iteration)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def retained_fragments(
        self,
        payload: DeepResearchPayload,
        *,
        iteration: int = 1,
    ) -> list[tuple[DeepResearchFragmentHit, str]]:
        """Use in-memory text, or rehydrate it from scoped SQLite after restart."""

        snapshot = self.load(payload, iteration=iteration)
        key = self._run_key(payload, iteration)
        texts = self._retained_texts.get(key, {})
        missing = [hit for hit in snapshot.hits if self._identity(hit) not in texts]
        if missing:
            if self.text_loader is None:
                raise RuntimeError("retained deep-research fragment text is unavailable")
            texts = {**texts, **self.text_loader.load(missing)}
            self._retained_texts[key] = texts
        fragments: list[tuple[DeepResearchFragmentHit, str]] = []
        for hit in snapshot.hits:
            text = texts.get(self._identity(hit))
            if text is None or _text_hash(text) != hit.text_sha256:
                raise RuntimeError("retained deep-research fragment hash mismatch")
            fragments.append((hit, text))
        return fragments

    def save_contextual_summaries(
        self,
        payload: DeepResearchPayload,
        summaries: list[ContextualSummaryResult],
        *,
        attempted: bool,
        evidence: ContextualEvidenceGate,
        iteration: int = 1,
    ) -> DeepResearchSearchSnapshot:
        snapshot = self.load(payload, iteration=iteration).model_copy(
            update={
                "contextual_summary_attempted": attempted,
                "contextual_summaries": summaries,
                "contextual_evidence": evidence,
            }
        )
        self._save(payload, snapshot)
        return snapshot

    def contextual_evidence(
        self,
        payload: DeepResearchPayload,
        *,
        iteration: int = 1,
    ) -> tuple[ContextualSummaryResult, ...]:
        """Expose only accepted contextual evidence to downstream operations."""

        gate = self.load(payload, iteration=iteration).contextual_evidence
        if gate is None:
            raise RuntimeError("contextual evidence gate has not run")
        return tuple(gate.accepted)

    def search(
        self,
        payload: DeepResearchPayload,
        *,
        query: str | None = None,
        iteration: int = 1,
        explicit_gap_id: str | None = None,
        explicit_gap_description: str | None = None,
    ) -> DeepResearchSearchSnapshot:
        if iteration == 1 and (explicit_gap_id or explicit_gap_description):
            raise ValueError("the original search cannot carry a research gap")
        if iteration == 2 and (not explicit_gap_id or not explicit_gap_description):
            raise ValueError("a follow-up search requires a persisted explicit gap")
        active_query = " ".join((query or payload.message).split())
        if len(active_query) < 2:
            raise ValueError("deep-research query is too short")
        scopes = (CorpusScope.COMMON, CorpusScope.PRIVATE)
        variants = build_bilingual_variants(active_query)
        candidates: dict[tuple[CorpusScope, str, int], _FusionCandidate] = {}
        raw_hit_count = 0

        def add_hit(
            *,
            method: Literal["lexical", "vector"],
            variant_index: int,
            query: str,
            scope: CorpusScope,
            article_id: str,
            chunk_id: int,
            page_start: int,
            page_end: int,
            text: str,
            rank: int,
            weight: float,
        ) -> None:
            key = (scope, article_id, chunk_id)
            text_sha256 = _text_hash(text)
            candidate = candidates.get(key)
            if candidate is None:
                candidate = _FusionCandidate(
                    scope=scope,
                    article_id=article_id,
                    chunk_id=chunk_id,
                    page_start=page_start,
                    page_end=page_end,
                    text=text,
                    text_sha256=text_sha256,
                )
                candidates[key] = candidate
            elif (
                candidate.text_sha256 != text_sha256
                or candidate.page_start != page_start
                or candidate.page_end != page_end
            ):
                raise RuntimeError("one scoped chunk has inconsistent full-text provenance")
            source = f"{method}:{variant_index}:{scope.value}"
            candidate.add_source(source, rank, query, weight)

        for variant_index, variant in enumerate(variants):
            lexical = self.lexical.search(
                variant.text,
                limit_per_scope=self.limit_per_scope,
                scopes=scopes,
            )
            vector = self.vector.search(
                variant.text,
                limit_per_scope=self.limit_per_scope,
                scopes=scopes,
            )
            for result in lexical.results:
                raw_hit_count += 1
                if not variant_matches_text(
                    variant,
                    result.text,
                    title=result.article_title,
                ):
                    continue
                add_hit(
                    method="lexical",
                    variant_index=variant_index,
                    query=variant.text,
                    scope=result.scope,
                    article_id=result.article_id,
                    chunk_id=result.chunk_id,
                    page_start=result.page_start,
                    page_end=result.page_end,
                    text=result.text,
                    rank=result.rank,
                    weight=query_variant_weight(variant),
                )

            vector_ranks = {scope: 0 for scope in scopes}
            for result in vector.results:
                raw_hit_count += 1
                if not variant_matches_text(variant, result.text):
                    continue
                vector_ranks[result.scope] += 1
                add_hit(
                    method="vector",
                    variant_index=variant_index,
                    query=variant.text,
                    scope=result.scope,
                    article_id=result.article_id,
                    chunk_id=result.chunk_id,
                    page_start=result.page_start,
                    page_end=result.page_end,
                    text=result.text,
                    rank=vector_ranks[result.scope],
                    weight=query_variant_weight(variant),
                )
        fused: list[tuple[DeepResearchFragmentHit, str]] = []
        for candidate in candidates.values():
            contributions = {
                source: candidate.source_weights[source] / (self.rrf_k + rank)
                for source, rank in candidate.source_ranks.items()
            }
            rrf_score = sum(contributions.values())
            fused.append(
                (
                    DeepResearchFragmentHit(
                        method="rrf",
                        scope=candidate.scope,
                        article_id=candidate.article_id,
                        chunk_id=candidate.chunk_id,
                        page_start=candidate.page_start,
                        page_end=candidate.page_end,
                        score=rrf_score,
                        rrf_score=rrf_score,
                        source_ranks=candidate.source_ranks,
                        source_contributions=contributions,
                        matched_queries=candidate.matched_queries,
                        text_sha256=candidate.text_sha256,
                    ),
                    candidate.text,
                )
            )
        scope_priority = {CorpusScope.COMMON: 0, CorpusScope.PRIVATE: 1}
        fused.sort(
            key=lambda item: (
                -item[0].rrf_score,
                scope_priority[item[0].scope],
                item[0].article_id,
                item[0].chunk_id,
            )
        )
        rrf_candidates = fused[: self.rrf_candidate_limit]
        cross_encoder_candidates = rrf_candidates[: self.cross_encoder_candidate_limit]
        if self.reranker and cross_encoder_candidates:
            reranker_candidates = [
                RerankerCandidate(
                    candidate_id=f"candidate-{index}",
                    text=text,
                    original_score=hit.rrf_score,
                )
                for index, (hit, text) in enumerate(cross_encoder_candidates)
            ]
            reranked = self.reranker.rerank(
                active_query,
                reranker_candidates,
                top_k=self.retained_fragment_limit,
            )
            hit_lookup = {
                candidate.candidate_id: hit
                for candidate, (hit, _text) in zip(
                    reranker_candidates,
                    cross_encoder_candidates,
                    strict=True,
                )
            }
            hits = [
                hit_lookup[result.candidate_id].model_copy(
                    update={
                        "rank": rank,
                        "score": result.combined_score,
                        "rerank_score": result.rerank_score,
                    }
                )
                for rank, result in enumerate(reranked, start=1)
            ]
        else:
            hits = [
                hit.model_copy(update={"rank": rank})
                for rank, (hit, _text) in enumerate(
                    cross_encoder_candidates[: self.retained_fragment_limit],
                    start=1,
                )
            ]

        snapshot = DeepResearchSearchSnapshot(
            iteration=iteration,
            query=active_query,
            explicit_gap_id=explicit_gap_id,
            explicit_gap_description=explicit_gap_description,
            scopes=list(scopes),
            variants=variants,
            hits=hits,
            raw_hit_count=raw_hit_count,
            fused_candidate_count=len(fused),
            rrf_candidate_count=len(rrf_candidates),
            cross_encoder_candidate_count=len(cross_encoder_candidates),
            rrf_k=self.rrf_k,
            reranker_enabled=bool(self.reranker and self.reranker.enabled),
        )
        retained_identities = {self._identity(hit) for hit in hits}
        self._retained_texts[self._run_key(payload, iteration)] = {
            self._identity(hit): text
            for hit, text in fused
            if self._identity(hit) in retained_identities
        }
        self._save(payload, snapshot)
        return snapshot
