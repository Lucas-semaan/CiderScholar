from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.chat_effort import AnswerEffort
from app.llm.argo_client import ArgoProtocolError
from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.retrieval.hypothesis_planning import (
    ArgoHypothesisPlanningService,
    deterministic_hypothesis_plan,
)


def _metrics() -> GenerationMetrics:
    return GenerationMetrics(
        total_duration_seconds=1,
        load_duration_seconds=0,
        prompt_eval_count=10,
        prompt_eval_duration_seconds=0.5,
        eval_count=5,
        eval_duration_seconds=0.5,
    )


class _Client:
    def __init__(self, payloads: Sequence[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.options: list[dict[str, Any]] = []
        self.messages: list[Sequence[Mapping[str, str]]] = []

    def chat(self, messages: Sequence[Mapping[str, str]], **options: Any) -> GenerationResponse:
        self.messages.append(messages)
        self.options.append(options)
        return GenerationResponse(
            model="argo-test",
            content=json.dumps(self.payloads.pop(0), ensure_ascii=False),
            done_reason="stop",
            metrics=_metrics(),
        )


def _payload() -> dict[str, Any]:
    return {
        "interpreted_question": "Effet du cuvage avant pressurage sur le cidre.",
        "hypothetical_answer": (
            "Une réponse experte chercherait à relier le cuvage au rendement en jus et aux "
            "transformations des composés phénoliques, en séparant observation et mécanisme."
        ),
        "concept_definition": "Maintien des pommes avant leur pressurage.",
        "ambiguities": [],
        "excluded_concepts": ["stockage du cidre fini"],
        "matrix_primary": ["pomme à cidre"],
        "matrix_close": ["jus de pomme"],
        "matrix_distant": [],
        "process_terms_fr": ["cuvage des pommes"],
        "process_terms_en": ["apple pre-press holding"],
        "verification_needs": [
            {
                "need_id": "v1",
                "claim_to_verify": "Le cuvage modifie le rendement en jus.",
                "evidence_required": "Mesure comparative du rendement au pressurage.",
                "search_query": "apple pre-press holding juice yield",
                "contradiction_query": None,
            },
            {
                "need_id": "v2",
                "claim_to_verify": "Le cuvage modifie les composés phénoliques.",
                "evidence_required": "Dosage comparatif des composés phénoliques.",
                "search_query": "apple pre-press holding phenolic compounds",
                "contradiction_query": None,
            },
        ],
    }


def test_hypothesis_planner_builds_adaptive_non_axis_grouped_queries() -> None:
    client = _Client([_payload()])
    result = ArgoHypothesisPlanningService(client).plan(
        "Quels effets le cuvage a-t-il avant pressurage ?",
        effort=AnswerEffort.CONCISE,
    )

    assert [need.need_id for need in result.plan.verification_needs] == ["v1", "v2"]
    queries = result.plan.retrieval_queries(
        "Quels effets le cuvage a-t-il avant pressurage ?",
        limit=5,
    )
    assert queries[0] == "Quels effets le cuvage a-t-il avant pressurage ?"
    assert queries[1] == result.plan.hypothetical_answer
    assert queries[2:] == [
        "apple pre-press holding juice yield",
        "apple pre-press holding phenolic compounds",
    ]
    assert client.options[0]["json_schema"]["properties"]["verification_needs"]["maxItems"] == 3
    assert "ni des axes de travail" in client.messages[0][0]["content"]


def test_hypothesis_planner_rejects_unverified_numbers_after_one_correction() -> None:
    invalid = _payload()
    invalid["hypothetical_answer"] += " Un gain de 25 % serait attendu."
    client = _Client([invalid, invalid])

    with pytest.raises(ArgoProtocolError, match="invalid hypothetical research plan"):
        ArgoHypothesisPlanningService(client).plan(
            "Quels effets le cuvage a-t-il avant pressurage ?",
            effort=AnswerEffort.BALANCED,
        )

    assert len(client.options) == 2


def test_deterministic_hypothesis_fallback_contains_no_scientific_conclusion() -> None:
    result = deterministic_hypothesis_plan(
        "Quels effets le cuvage a-t-il avant pressurage ?",
        effort=AnswerEffort.DEEP,
    )

    assert result.used_fallback is True
    assert len(result.plan.verification_needs) == 1
    assert "sans présumer de leur conclusion" in result.plan.hypothetical_answer
