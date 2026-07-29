from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from app.corpora import CorpusScope
from app.deep_research.abstention import DeepResearchAbstentionStage
from app.deep_research.admission import ClaimAdmissionStage
from app.deep_research.citations import (
    CitationTraversalStage,
    ResolvedCitationTarget,
)
from app.deep_research.claims import AtomicClaimExtractionStage
from app.deep_research.contextual_summary import ContextualSummarizer
from app.deep_research.epistemic import EpistemicAssessmentStage
from app.deep_research.iteration import (
    MAX_RESEARCH_ITERATIONS,
    MissingInformationAssessment,
    ResearchLoopStore,
)
from app.deep_research.pipeline import DeepResearchPreparationOperations
from app.deep_research.retrieval import DeepResearchRetrievalStage
from app.deep_research.verification import SemanticClaimVerificationStage
from app.jobs.contracts import DeepResearchPayload
from app.retrieval.lexical_search import LexicalSearchResult
from app.retrieval.multi_corpus import MultiCorpusLexicalResponse, MultiCorpusVectorResponse


class _Lexical:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query, *, limit_per_scope=None, scopes=()):
        self.queries.append(query)
        return MultiCorpusLexicalResponse(
            query=query,
            results=[
                LexicalSearchResult(
                    rank=1,
                    chunk_id=1,
                    article_id="article-loop",
                    article_title="Boucle",
                    publication_year=2026,
                    section="Results",
                    page_start=2,
                    page_end=2,
                    text="Texte local stable pour la recherche itérative.",
                    bm25_score=-1.0,
                    relevance_score=0.9,
                    scope=CorpusScope.COMMON,
                )
            ],
            duration_seconds_by_scope={scope: 0.01 for scope in scopes},
            duration_seconds=0.02,
        )


class _Vector:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query, *, limit_per_scope=None, scopes=()):
        self.queries.append(query)
        return MultiCorpusVectorResponse(
            query=query,
            results=[],
            duration_seconds_by_scope={scope: 0.01 for scope in scopes},
            duration_seconds=0.02,
        )


class _SummaryClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, _messages, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=json.dumps(
                {
                    "summary": "Preuve contextuelle acceptée.",
                    "relevance_score": 0.9,
                }
            )
        )


class _GapAssessor:
    def __init__(self, assessment: MissingInformationAssessment) -> None:
        self.assessment = assessment
        self.calls = 0

    def assess(self, _question, _evidence):
        self.calls += 1
        return self.assessment


class _CitationResolver:
    def resolve(self, *, target_doi, **_kwargs):
        return ResolvedCitationTarget(
            target_doi=target_doi,
            access_status="unavailable",
        )


class _Renderer:
    pass


def _payload() -> DeepResearchPayload:
    return DeepResearchPayload(
        message="Quel paramètre manque dans les preuves ?",
        conversation_id=uuid4(),
        client_request_id=uuid4(),
    )


def _operations(
    tmp_path,
    assessment: MissingInformationAssessment,
) -> tuple[DeepResearchPreparationOperations, _Lexical, _GapAssessor]:
    lexical = _Lexical()
    vector = _Vector()
    assessor = _GapAssessor(assessment)
    operations = DeepResearchPreparationOperations(
        retrieval=DeepResearchRetrievalStage(lexical, vector, tmp_path),
        contextual_summarizer=ContextualSummarizer(_SummaryClient()),
        research_loop=ResearchLoopStore(tmp_path),
        citation_traversal=CitationTraversalStage(_CitationResolver(), tmp_path),
        claim_extraction=AtomicClaimExtractionStage(None, tmp_path),
        claim_verification=SemanticClaimVerificationStage(None, tmp_path),
        epistemic_assessment=EpistemicAssessmentStage(tmp_path),
        claim_admission=ClaimAdmissionStage(tmp_path),
        abstention=DeepResearchAbstentionStage(tmp_path),
        renderer=_Renderer(),
        response_cache=None,
        gap_assessor=assessor,
    )
    return operations, lexical, assessor


def test_insufficient_evidence_runs_one_explicit_follow_up_then_stops(tmp_path) -> None:
    payload = _payload()
    operations, lexical, assessor = _operations(
        tmp_path,
        MissingInformationAssessment(
            sufficient=False,
            gap_description="La température de fermentation n'est pas documentée.",
            follow_up_query="température fermentation cidre résultats",
        ),
    )

    operations.search(payload)
    operations.extract_evidence(payload)
    loop = operations.research_loop.load_or_create(payload)
    second = operations.retrieval.load(payload, iteration=2)
    query_count = len(lexical.queries)

    assert len(loop.iterations) == MAX_RESEARCH_ITERATIONS
    assert loop.stop_reason == "maximum_iterations"
    assert loop.iterations[1].gap is not None
    assert second.query == loop.iterations[1].query
    assert second.explicit_gap_id == loop.iterations[1].gap.gap_id
    assert second.explicit_gap_description == loop.iterations[1].gap.description
    assert assessor.calls == 1

    operations.extract_evidence(payload)
    assert len(lexical.queries) == query_count
    assert assessor.calls == 1


def test_sufficient_evidence_stops_after_original_search(tmp_path) -> None:
    payload = _payload()
    operations, _lexical, assessor = _operations(
        tmp_path,
        MissingInformationAssessment(sufficient=True),
    )

    operations.search(payload)
    operations.extract_evidence(payload)
    loop = operations.research_loop.load_or_create(payload)

    assert len(loop.iterations) == 1
    assert loop.stop_reason == "sufficient_evidence"
    assert operations.retrieval.exists(payload, iteration=2) is False
    assert assessor.calls == 1


def test_duplicate_follow_up_query_is_rejected_without_second_search(tmp_path) -> None:
    payload = _payload()
    operations, _lexical, _assessor = _operations(
        tmp_path,
        MissingInformationAssessment(
            sufficient=False,
            gap_description="La première recherche paraît incomplète.",
            follow_up_query=payload.message,
        ),
    )

    operations.search(payload)
    operations.extract_evidence(payload)
    loop = operations.research_loop.load_or_create(payload)

    assert len(loop.iterations) == 1
    assert loop.stop_reason == "no_valid_gap"
    assert operations.retrieval.exists(payload, iteration=2) is False
