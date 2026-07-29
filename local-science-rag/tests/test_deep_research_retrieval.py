from __future__ import annotations

from uuid import uuid4

from app.corpora import CorpusScope
from app.deep_research.retrieval import DeepResearchRetrievalStage
from app.jobs.contracts import DeepResearchPayload
from app.retrieval.lexical_search import LexicalSearchResult
from app.retrieval.multi_corpus import MultiCorpusLexicalResponse, MultiCorpusVectorResponse
from app.retrieval.vector_search import VectorSearchResult


class FakeLexical:
    def __init__(self) -> None:
        self.scopes: tuple[CorpusScope, ...] = ()

    def search(self, query, *, limit_per_scope=None, scopes=()):
        self.scopes = scopes
        assert limit_per_scope == 40
        return MultiCorpusLexicalResponse(
            query=query,
            results=[
                LexicalSearchResult(
                    rank=1,
                    chunk_id=1,
                    article_id="common-article",
                    article_title="Commun",
                    publication_year=2025,
                    section="Results",
                    page_start=2,
                    page_end=2,
                    text="common private sentinel must not enter checkpoint",
                    bm25_score=-1.0,
                    relevance_score=0.9,
                    scope=CorpusScope.COMMON,
                )
            ],
            duration_seconds_by_scope={CorpusScope.COMMON: 0.1, CorpusScope.PRIVATE: 0.1},
            duration_seconds=0.2,
        )


class FakeVector:
    def __init__(self) -> None:
        self.scopes: tuple[CorpusScope, ...] = ()

    def search(self, query, *, limit_per_scope=None, scopes=()):
        self.scopes = scopes
        assert limit_per_scope == 40
        return MultiCorpusVectorResponse(
            query=query,
            results=[
                VectorSearchResult(
                    chunk_id=7,
                    article_id="private-article",
                    score=0.8,
                    section="Discussion",
                    page_start=4,
                    page_end=5,
                    text="private full text sentinel",
                    scope=CorpusScope.PRIVATE,
                )
            ],
            duration_seconds_by_scope={CorpusScope.COMMON: 0.1, CorpusScope.PRIVATE: 0.1},
            duration_seconds=0.2,
        )


def test_deep_research_queries_both_scopes_and_persists_only_scoped_references(
    tmp_path,
) -> None:
    lexical = FakeLexical()
    vector = FakeVector()
    payload = DeepResearchPayload(
        message="Quel effet de l'oxygénation sur le cidre ?",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )
    snapshot = DeepResearchRetrievalStage(lexical, vector, tmp_path).search(payload)

    assert lexical.scopes == (CorpusScope.COMMON, CorpusScope.PRIVATE)
    assert vector.scopes == (CorpusScope.COMMON, CorpusScope.PRIVATE)
    assert len(snapshot.variants) >= 1
    assert snapshot.variants[0].derivation == "original"
    assert {(hit.scope, hit.chunk_id) for hit in snapshot.hits} == {
        (CorpusScope.COMMON, 1),
        (CorpusScope.PRIVATE, 7),
    }
    assert all(hit.method == "rrf" for hit in snapshot.hits)
    assert all(hit.rrf_score == sum(hit.source_contributions.values()) for hit in snapshot.hits)
    assert all(
        contribution == 1 / (snapshot.rrf_k + hit.source_ranks[source])
        for hit in snapshot.hits
        for source, contribution in hit.source_contributions.items()
    )
    assert snapshot.raw_hit_count >= snapshot.fused_candidate_count == 2
    assert snapshot.rrf_candidate_count == snapshot.cross_encoder_candidate_count == 2
    persisted = next(tmp_path.rglob("retrieval.json")).read_text(encoding="utf-8")
    assert "sentinel" not in persisted
    assert '"scope": "common"' in persisted
    assert '"scope": "private"' in persisted
    assert '"variants"' in persisted


def test_deep_research_applies_optional_reranker(tmp_path) -> None:
    from unittest.mock import MagicMock

    from app.retrieval.reranker import MultilingualReranker, RerankedResult

    lexical = FakeLexical()
    vector = FakeVector()
    mock_reranker = MagicMock(spec=MultilingualReranker)
    mock_reranker.enabled = True

    def rerank(_query, candidates, *, top_k=None):
        results = [
            RerankedResult(
                candidate_id=candidate.candidate_id,
                text=candidate.text,
                original_score=candidate.original_score,
                rerank_score=0.99,
                combined_score=0.99,
            )
            for candidate in candidates
        ]
        return results[:top_k]

    mock_reranker.rerank.side_effect = rerank

    payload = DeepResearchPayload(
        message="Test reranking",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )
    stage = DeepResearchRetrievalStage(lexical, vector, tmp_path, reranker=mock_reranker)
    snapshot = stage.search(payload)

    assert mock_reranker.rerank.called
    assert all(hit.score == 0.99 for hit in snapshot.hits)
    assert all(hit.rerank_score == 0.99 for hit in snapshot.hits)


def test_rrf_bounds_candidates_before_reranking_and_retains_configured_count(
    tmp_path,
) -> None:
    from unittest.mock import MagicMock

    from app.retrieval.reranker import MultilingualReranker, RerankedResult

    class ManyLexical:
        def search(self, query, *, limit_per_scope=None, scopes=()):
            return MultiCorpusLexicalResponse(
                query=query,
                results=[
                    LexicalSearchResult(
                        rank=rank,
                        chunk_id=rank,
                        article_id=f"article-{rank}",
                        article_title=f"Article {rank}",
                        publication_year=2026,
                        section="Results",
                        page_start=rank,
                        page_end=rank,
                        text=f"fragment {rank}",
                        bm25_score=float(-rank),
                        relevance_score=10_000.0 - rank,
                        scope=CorpusScope.COMMON,
                    )
                    for rank in range(1, 7)
                ],
                duration_seconds_by_scope={scope: 0.01 for scope in scopes},
                duration_seconds=0.02,
            )

    class EmptyVector:
        def search(self, query, *, limit_per_scope=None, scopes=()):
            return MultiCorpusVectorResponse(
                query=query,
                results=[],
                duration_seconds_by_scope={scope: 0.01 for scope in scopes},
                duration_seconds=0.02,
            )

    reranker = MagicMock(spec=MultilingualReranker)
    reranker.enabled = True

    def rerank(_query, candidates, *, top_k=None):
        assert len(candidates) == 3
        return [
            RerankedResult(
                candidate_id=candidate.candidate_id,
                text=candidate.text,
                original_score=candidate.original_score,
                rerank_score=float(index),
                combined_score=float(index),
            )
            for index, candidate in enumerate(candidates, start=1)
        ][:top_k]

    reranker.rerank.side_effect = rerank
    payload = DeepResearchPayload(
        message="Question sans terme du lexique",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )
    stage = DeepResearchRetrievalStage(
        ManyLexical(),
        EmptyVector(),
        tmp_path,
        reranker=reranker,
        rrf_candidate_limit=4,
        cross_encoder_candidate_limit=3,
        retained_fragment_limit=2,
    )

    snapshot = stage.search(payload)

    assert snapshot.fused_candidate_count == 6
    assert snapshot.rrf_candidate_count == 4
    assert snapshot.cross_encoder_candidate_count == 3
    assert len(snapshot.hits) == 2
    assert [hit.rank for hit in snapshot.hits] == [1, 2]
