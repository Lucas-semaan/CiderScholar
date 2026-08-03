from __future__ import annotations

import json

import pytest

from app.llm.argo_client import ArgoQuotaError
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.query_planning import ResearchAxis
from app.retrieval.semantic_filter import (
    ArgoSemanticEvidenceFilter,
    SemanticFilterResult,
)


def _response(content: dict[str, object]) -> GenerationResponse:
    return GenerationResponse(
        model="semantic-test",
        content=json.dumps(content),
        done_reason="stop",
        metrics=GenerationMetrics(
            total_duration_seconds=0.1,
            load_duration_seconds=0,
            prompt_eval_count=100,
            prompt_eval_duration_seconds=0.01,
            eval_count=50,
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


def _axis(key: str = "haze") -> ResearchAxis:
    return ResearchAxis(
        key=key,
        label="Trouble protéique",
        question="Quels mécanismes gouvernent le trouble protéique ?",
        terms_fr=["trouble protéique", "stabilité colloïdale"],
        terms_en=["protein haze", "colloidal stability"],
        search_queries=["apple juice protein haze colloidal stability"],
    )


def _record(record_id: str, title: str, text: str) -> ChatEvidenceRecord:
    return ChatEvidenceRecord(
        record_id=record_id,
        origin="local_rag",
        evidence_level="abstract",
        title=title,
        passages=[
            ChatEvidencePassage(
                evidence_id=f"{record_id}:abstract",
                section="abstract",
                text=text,
            )
        ],
    )


def test_semantic_filter_uses_meaning_and_selects_direct_and_supportive_candidates() -> None:
    client = _Client(
        [
            {
                "axis_key": "haze",
                "decisions": [
                    {
                        "candidate_id": "fr",
                        "relevance": "direct",
                        "rationale": "Étudie directement le trouble protéique.",
                        "matched_concepts": ["protein haze", "trouble protéique"],
                    },
                    {
                        "candidate_id": "en",
                        "relevance": "supportive",
                        "rationale": "Décrit une agrégation protéines-polyphénols connexe.",
                        "matched_concepts": ["protein-polyphenol aggregation"],
                    },
                    {
                        "candidate_id": "noise",
                        "relevance": "irrelevant",
                        "rationale": "Porte uniquement sur les sucres.",
                        "matched_concepts": [],
                    },
                ],
            }
        ]
    )
    records = [
        _record("fr", "Stabilité du jus", "Formation d'un trouble protéique dans le jus."),
        _record("en", "Cloud formation", "Protein-polyphenol aggregation in beverages."),
        _record("noise", "FTIR sugars", "Glucose and fructose quantification."),
    ]

    result = ArgoSemanticEvidenceFilter(client).filter_records(
        "État de l'art sur la stabilité protéique des jus de pomme",
        [_axis()],
        records,
    )

    assert result.selected_candidate_ids == ["fr", "en"]
    assert [record.record_id for record in result.selected_records(records)] == ["fr", "en"]
    assert result.used_fallback is False
    system_prompt = client.calls[0][0][0]["content"]
    assert "jamais par simple présence de mots-clés" in system_prompt
    assert "traductions entre langues" in system_prompt
    schema = client.calls[0][1]["json_schema"]
    decision = schema["$defs"]["CandidateSemanticDecision"]
    assert decision["properties"]["candidate_id"]["enum"] == ["fr", "en", "noise"]
    assert "unassessed" not in decision["properties"]["relevance"]["enum"]


def test_semantic_filter_retries_invalid_ids_then_falls_back_without_dropping_evidence() -> None:
    invalid = {
        "axis_key": "haze",
        "decisions": [
            {
                "candidate_id": "invented-reference",
                "relevance": "direct",
                "rationale": "Invalid id",
                "matched_concepts": [],
            }
        ],
    }
    client = _Client([invalid, invalid])
    records = [_record("persisted", "Apple haze", "Haze-active apple proteins.")]

    result = ArgoSemanticEvidenceFilter(client).filter_records(
        "Stabilité protéique",
        [_axis()],
        records,
    )

    assert result.used_fallback is True
    assert result.selected_candidate_ids == ["persisted"]
    assert result.axes[0].decisions[0].relevance == "unassessed"
    assert len(client.calls) == 2


def test_semantic_filter_api_failure_is_a_safe_recall_preserving_fallback() -> None:
    records = [_record("persisted", "Apple haze", "Haze-active apple proteins.")]
    result = ArgoSemanticEvidenceFilter(_Client([TimeoutError("offline")])).filter_records(
        "Stabilité protéique",
        [_axis()],
        records,
    )

    assert result.model == "safe-fallback"
    assert result.selected_candidate_ids == ["persisted"]
    assert result.warnings == [
        "Semantic filtering unavailable for axis haze (TimeoutError); candidates retained."
    ]


def test_semantic_filter_propagates_quota_for_deferred_retry() -> None:
    records = [_record("persisted", "Apple haze", "Haze-active apple proteins.")]

    with pytest.raises(ArgoQuotaError):
        ArgoSemanticEvidenceFilter(_Client([ArgoQuotaError("quota")])).filter_records(
            "Stabilité protéique",
            [_axis()],
            records,
        )


def test_semantic_filter_contract_forbids_unknown_fields() -> None:
    payload = {
        "question": "question",
        "axes": [],
        "selected_candidate_ids": [],
        "model": "test",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "unknown": True,
    }

    try:
        SemanticFilterResult.model_validate(payload)
    except Exception:
        pass
    else:
        raise AssertionError("unknown contract fields must be rejected")
