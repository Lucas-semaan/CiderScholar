from __future__ import annotations

import json

import pytest

from app.llm.argo_client import ArgoQuotaError
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.coverage_assessment import ArgoEvidenceCoverageAssessor
from app.retrieval.query_planning import ResearchAxis
from app.retrieval.semantic_filter import (
    AxisSemanticAssessment,
    CandidateSemanticDecision,
    SemanticFilterResult,
)


def _response(content: dict[str, object]) -> GenerationResponse:
    return GenerationResponse(
        model="coverage-test",
        content=json.dumps(content),
        done_reason="stop",
        metrics=GenerationMetrics(
            total_duration_seconds=0.1,
            load_duration_seconds=0,
            prompt_eval_count=80,
            prompt_eval_duration_seconds=0.01,
            eval_count=40,
            eval_duration_seconds=0.01,
        ),
    )


class _Client:
    def __init__(self, payloads: list[dict[str, object] | Exception]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[object, dict[str, object]]] = []

    def chat(self, messages, **options):
        self.calls.append((messages, options))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return _response(payload)


def _axis(key: str, label: str) -> ResearchAxis:
    return ResearchAxis(
        key=key,
        label=label,
        question=f"Que sait-on sur {label} ?",
        terms_fr=[label],
        terms_en=[label],
        search_queries=[f"apple juice {label}"],
    )


def _record(record_id: str) -> ChatEvidenceRecord:
    return ChatEvidenceRecord(
        record_id=record_id,
        origin="local_rag",
        evidence_level="abstract",
        title="Persisted evidence",
        passages=[
            ChatEvidencePassage(
                evidence_id=f"{record_id}:abstract",
                section="abstract",
                text="Apple proteins and polyphenols form haze during storage.",
            )
        ],
    )


def _semantic(axes: list[ResearchAxis], record_id: str) -> SemanticFilterResult:
    return SemanticFilterResult(
        question="Stabilité protéique du jus de pomme",
        axes=[
            AxisSemanticAssessment(
                axis_key=axis.key,
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id=record_id,
                        relevance="direct" if index == 0 else "supportive",
                        rationale="Relevant persisted evidence.",
                    )
                ],
            )
            for index, axis in enumerate(axes)
        ],
        selected_candidate_ids=[record_id],
        model="semantic-test",
        prompt_tokens=10,
        completion_tokens=10,
    )


def test_coverage_assessor_reports_covered_and_missing_axes_with_grounded_ids() -> None:
    axes = [_axis("mechanism", "mécanismes"), _axis("methods", "méthodes")]
    mechanism_payload = {
        "axes": [
            {
                "axis_key": "mechanism",
                "status": "covered",
                "supporting_candidate_ids": ["record-1"],
                "assessment": "La preuve traite directement le mécanisme.",
                "missing_information": [],
                "suggested_queries": [],
            }
        ]
    }
    methods_payload = {
        "axes": [
            {
                "axis_key": "methods",
                "status": "missing",
                "supporting_candidate_ids": [],
                "assessment": "La méthode analytique n'est pas documentée.",
                "missing_information": ["Méthodes de mesure"],
                "suggested_queries": ["apple juice haze analytical methods"],
            },
        ]
    }
    client = _Client([mechanism_payload, methods_payload])

    result = ArgoEvidenceCoverageAssessor(client).assess(
        "Stabilité protéique du jus de pomme",
        axes,
        [_record("record-1")],
        _semantic(axes, "record-1"),
    )

    assert result.covered_axis_keys == ["mechanism"]
    assert result.missing_axis_keys == ["methods"]
    assert result.ready_for_synthesis is False
    assert result.used_fallback is False
    schema = client.calls[0][1]["json_schema"]
    axis_schema = schema["$defs"]["AxisCoverageAssessment"]
    assert axis_schema["properties"]["supporting_candidate_ids"]["items"]["enum"] == ["record-1"]
    assert len(client.calls) == 2


def test_coverage_assessor_keeps_more_than_twenty_eligible_candidates_bounded() -> None:
    axis = _axis("mechanism", "mécanismes")
    records = [_record(f"record-{index}") for index in range(36)]
    semantic = SemanticFilterResult(
        question="Stabilité protéique du jus de pomme",
        axes=[
            AxisSemanticAssessment(
                axis_key=axis.key,
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id=record.record_id,
                        relevance="direct",
                        rationale="Relevant persisted evidence.",
                    )
                    for record in records
                ],
            )
        ],
        selected_candidate_ids=[record.record_id for record in records],
        model="semantic-test",
        prompt_tokens=10,
        completion_tokens=10,
    )
    client = _Client(
        [
            {
                "axes": [
                    {
                        "axis_key": axis.key,
                        "status": "covered",
                        "supporting_candidate_ids": [record.record_id for record in records],
                        "assessment": "Les preuves directes couvrent le mécanisme.",
                        "missing_information": [],
                        "suggested_queries": [],
                    }
                ]
            }
        ]
    )

    result = ArgoEvidenceCoverageAssessor(client).assess(
        "Stabilité protéique du jus de pomme", [axis], records, semantic
    )

    assert result.axes[0].status == "covered"
    payload = json.loads(client.calls[0][0][1]["content"])
    assert [candidate["candidate_id"] for candidate in payload["candidates"]] == [
        record.record_id for record in records
    ]
    assert len(client.calls[0][0][1]["content"]) <= 48_000


def test_coverage_rejects_invented_or_semantically_ineligible_references() -> None:
    axes = [_axis("mechanism", "mécanismes")]
    invalid = {
        "axes": [
            {
                "axis_key": "mechanism",
                "status": "covered",
                "supporting_candidate_ids": ["invented"],
                "assessment": "Invalid.",
                "missing_information": [],
                "suggested_queries": [],
            }
        ]
    }
    client = _Client([invalid, invalid])

    result = ArgoEvidenceCoverageAssessor(client).assess(
        "Stabilité protéique du jus de pomme",
        axes,
        [_record("record-1")],
        _semantic(axes, "record-1"),
    )

    assert result.used_fallback is True
    assert result.axes[0].status == "indeterminate"
    assert result.axes[0].supporting_candidate_ids == []
    assert result.ready_for_synthesis is False
    assert len(client.calls) == 2


def test_coverage_uses_disjoint_candidates_per_axis_and_keeps_exact_axis_order() -> None:
    axes = [_axis("mechanism", "mécanismes"), _axis("methods", "méthodes")]
    semantic = SemanticFilterResult(
        question="Stabilité protéique du jus de pomme",
        axes=[
            AxisSemanticAssessment(
                axis_key="mechanism",
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id="record-mechanism",
                        relevance="direct",
                        rationale="Direct mechanism evidence.",
                    )
                ],
            ),
            AxisSemanticAssessment(
                axis_key="methods",
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id="record-methods",
                        relevance="direct",
                        rationale="Direct methods evidence.",
                    )
                ],
            ),
        ],
        selected_candidate_ids=["record-mechanism", "record-methods"],
        model="semantic-test",
        prompt_tokens=10,
        completion_tokens=10,
    )
    client = _Client(
        [
            {
                "axes": [
                    {
                        "axis_key": "mechanism",
                        "status": "covered",
                        "supporting_candidate_ids": ["record-mechanism"],
                        "assessment": "Direct mechanism evidence is available.",
                        "missing_information": [],
                        "suggested_queries": [],
                    }
                ]
            },
            {
                "axes": [
                    {
                        "axis_key": "methods",
                        "status": "covered",
                        "supporting_candidate_ids": ["record-methods"],
                        "assessment": "Direct methods evidence is available.",
                        "missing_information": [],
                        "suggested_queries": [],
                    }
                ]
            },
        ]
    )

    result = ArgoEvidenceCoverageAssessor(client).assess(
        "Stabilité protéique du jus de pomme",
        axes,
        [_record("record-mechanism"), _record("record-methods")],
        semantic,
    )

    assert [assessment.axis_key for assessment in result.axes] == ["mechanism", "methods"]
    assert result.covered_axis_keys == ["mechanism", "methods"]
    assert result.ready_for_synthesis is True
    for call, candidate_id in zip(
        client.calls, ["record-mechanism", "record-methods"], strict=True
    ):
        schema = call[1]["json_schema"]
        axis_schema = schema["$defs"]["AxisCoverageAssessment"]
        assert axis_schema["properties"]["supporting_candidate_ids"]["items"]["enum"] == [
            candidate_id
        ]
        payload = json.loads(call[0][1]["content"])
        assert payload["axis"]["semantically_eligible_candidate_ids"] == [candidate_id]
        assert [candidate["candidate_id"] for candidate in payload["candidates"]] == [candidate_id]


def test_coverage_falls_back_only_for_the_failed_axis() -> None:
    axes = [_axis("mechanism", "mécanismes"), _axis("methods", "méthodes")]
    semantic = SemanticFilterResult(
        question="Stabilité protéique du jus de pomme",
        axes=[
            AxisSemanticAssessment(
                axis_key="mechanism",
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id="record-mechanism",
                        relevance="direct",
                        rationale="Direct mechanism evidence.",
                    )
                ],
            ),
            AxisSemanticAssessment(
                axis_key="methods",
                decisions=[
                    CandidateSemanticDecision(
                        candidate_id="record-methods",
                        relevance="supportive",
                        rationale="Supportive methods evidence.",
                    )
                ],
            ),
        ],
        selected_candidate_ids=["record-mechanism", "record-methods"],
        model="semantic-test",
        prompt_tokens=10,
        completion_tokens=10,
    )
    client = _Client(
        [
            {
                "axes": [
                    {
                        "axis_key": "mechanism",
                        "status": "covered",
                        "supporting_candidate_ids": ["record-mechanism"],
                        "assessment": "Direct mechanism evidence is available.",
                        "missing_information": [],
                        "suggested_queries": [],
                    }
                ]
            },
            TimeoutError("offline"),
        ]
    )

    result = ArgoEvidenceCoverageAssessor(client).assess(
        "Stabilité protéique du jus de pomme",
        axes,
        [_record("record-mechanism"), _record("record-methods")],
        semantic,
    )

    assert result.used_fallback is False
    assert result.axes[0].status == "covered"
    assert result.axes[1].status == "partial"
    assert result.axes[1].supporting_candidate_ids == ["record-methods"]
    assert result.ready_for_synthesis is False
    assert result.warning is not None


def test_coverage_api_failure_never_assumes_evidence_is_sufficient() -> None:
    axes = [_axis("mechanism", "mécanismes")]
    result = ArgoEvidenceCoverageAssessor(_Client([TimeoutError("offline")])).assess(
        "Stabilité protéique du jus de pomme",
        axes,
        [_record("record-1")],
        _semantic(axes, "record-1"),
    )

    assert result.used_fallback is True
    assert result.axes[0].status == "indeterminate"
    assert result.missing_axis_keys == ["mechanism"]
    assert result.ready_for_synthesis is False


def test_coverage_stays_indeterminate_when_semantic_filter_used_fallback() -> None:
    axes = [_axis("mechanism", "mécanismes")]
    semantic = _semantic(axes, "record-1").model_copy(update={"used_fallback": True})
    semantic.axes[0].used_fallback = True
    client = _Client([])

    result = ArgoEvidenceCoverageAssessor(client).assess(
        "Stabilité protéique du jus de pomme",
        axes,
        [_record("record-1")],
        semantic,
    )

    assert result.used_fallback is True
    assert result.axes[0].status == "indeterminate"
    assert result.ready_for_synthesis is False
    assert client.calls == []


def test_coverage_propagates_quota_for_deferred_retry() -> None:
    axes = [_axis("mechanism", "mécanismes")]

    with pytest.raises(ArgoQuotaError):
        ArgoEvidenceCoverageAssessor(_Client([ArgoQuotaError("quota")])).assess(
            "Stabilité protéique du jus de pomme",
            axes,
            [_record("record-1")],
            _semantic(axes, "record-1"),
        )
