from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.llm.contracts import GenerationMetrics, GenerationResponse
from app.models.chatbot import ChatEvidencePassage, ChatEvidenceRecord
from app.retrieval.global_semantic_filter import ArgoGlobalSemanticEvidenceFilter
from app.retrieval.hypothesis_planning import VerificationNeed


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.options: dict[str, Any] = {}

    def chat(self, _messages: Sequence[Mapping[str, str]], **options: Any) -> GenerationResponse:
        self.options = options
        return GenerationResponse(
            model="argo-semantic-test",
            content=json.dumps(self.payload),
            done_reason="stop",
            metrics=GenerationMetrics(
                total_duration_seconds=1,
                load_duration_seconds=0,
                prompt_eval_count=12,
                prompt_eval_duration_seconds=0.5,
                eval_count=6,
                eval_duration_seconds=0.5,
            ),
        )


def _record(identifier: str, text: str) -> ChatEvidenceRecord:
    return ChatEvidenceRecord(
        record_id=identifier,
        origin="local_rag",
        evidence_level="abstract",
        scope="common",
        title=f"Article {identifier}",
        passages=[ChatEvidencePassage(evidence_id=f"{identifier}:abstract", text=text)],
    )


def _need() -> VerificationNeed:
    return VerificationNeed(
        need_id="v1",
        claim_to_verify="Le cuvage modifie le rendement.",
        evidence_required="Mesure comparative du rendement.",
        search_query="apple pre-press holding juice yield",
    )


def test_global_semantic_filter_assigns_a_d_once_without_coverage_controller() -> None:
    direct = _record("common:1", "Pre-press holding changed apple juice yield.")
    peripheral = _record("common:2", "Finished cider was stored after fermentation.")
    client = _Client(
        {
            "decisions": [
                {
                    "candidate_id": direct.record_id,
                    "relevance": "direct",
                    "supported_need_ids": ["v1"],
                    "rationale": "Direct matrix, process and outcome.",
                },
                {
                    "candidate_id": peripheral.record_id,
                    "relevance": "peripheral",
                    "supported_need_ids": [],
                    "rationale": "Different process stage.",
                },
            ]
        }
    )

    result = ArgoGlobalSemanticEvidenceFilter(client).filter_records(
        "Quels effets le cuvage a-t-il sur le rendement ?",
        [_need()],
        [direct, peripheral],
    )

    selected = result.selected_records([direct, peripheral])
    assert [record.record_id for record in selected] == [direct.record_id]
    assert selected[0].evidence_grade == "A"
    assert result.prompt_tokens == 12
    schema = client.options["json_schema"]
    assert schema["properties"]["decisions"]["minItems"] == 2


def test_global_semantic_filter_corrects_one_incomplete_json_response() -> None:
    direct = _record("common:1", "Pre-press holding changed apple juice yield.")

    class CorrectingClient:
        def __init__(self) -> None:
            self.calls = 0
            self.maximum_output_tokens = 0

        def chat(self, messages, **options):
            self.calls += 1
            self.maximum_output_tokens = options["max_output_tokens"]
            if self.calls == 1:
                content = json.dumps({"decisions": []})
            else:
                assert "exactement une décision par candidate_id" in messages[-1]["content"]
                content = json.dumps(
                    {
                        "decisions": [
                            {
                                "candidate_id": direct.record_id,
                                "relevance": "direct",
                                "supported_need_ids": ["v1"],
                                "rationale": "Direct matrix, process and outcome.",
                            }
                        ]
                    }
                )
            return GenerationResponse(
                model="argo-semantic-test",
                content=content,
                done_reason="stop",
                metrics=GenerationMetrics(
                    total_duration_seconds=1,
                    load_duration_seconds=0,
                    prompt_eval_count=12,
                    prompt_eval_duration_seconds=0.5,
                    eval_count=6,
                    eval_duration_seconds=0.5,
                ),
            )

    client = CorrectingClient()
    result = ArgoGlobalSemanticEvidenceFilter(client).filter_records(
        "Quels effets le cuvage a-t-il sur le rendement ?",
        [_need()],
        [direct],
    )

    assert client.calls == 2
    assert client.maximum_output_tokens == 980
    assert result.used_fallback is False
    assert result.selected_candidate_ids == [direct.record_id]
    assert result.prompt_tokens == 24
    assert result.completion_tokens == 12


def test_global_semantic_filter_keeps_all_cd_verdicts_without_retry() -> None:
    records = [
        _record("common:1", "Yeast expressed stress genes under changing wine conditions."),
        _record("common:2", "Temperature affected yeast viability during fermentation."),
    ]

    class RejectingClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, **_options):
            self.calls += 1
            if self.calls > 1:
                assert "semantic_filter_empty" in messages[-1]["content"]
                assert "supportive=B" in messages[-1]["content"]
            return GenerationResponse(
                model="argo-semantic-test",
                content=json.dumps(
                    {
                        "decisions": [
                            {
                                "candidate_id": record.record_id,
                                "relevance": "peripheral",
                                "supported_need_ids": [],
                                "rationale": "Related but judged peripheral.",
                            }
                            for record in records
                        ]
                    }
                ),
                done_reason="stop",
                metrics=GenerationMetrics(
                    total_duration_seconds=1,
                    load_duration_seconds=0,
                    prompt_eval_count=12,
                    prompt_eval_duration_seconds=0.5,
                    eval_count=6,
                    eval_duration_seconds=0.5,
                ),
            )

    client = RejectingClient()
    result = ArgoGlobalSemanticEvidenceFilter(client).filter_records(
        "Comment les levures s'adaptent-elles au réchauffement climatique ?",
        [_need()],
        records,
    )

    assert client.calls == 1
    assert result.used_fallback is False
    assert result.selected_candidate_ids == []
    assert {decision.relevance for decision in result.decisions} == {"peripheral"}
    assert result.prompt_tokens == 12
    assert result.warnings == []


def test_global_semantic_filter_rejects_unpersisted_external_evidence() -> None:
    external = _record("external:1", "Some result.").model_copy(
        update={"origin": "external_api", "scope": None}
    )

    with pytest.raises(ValueError, match="SQLite-backed"):
        ArgoGlobalSemanticEvidenceFilter(_Client({})).filter_records(
            "Question scientifique ?",
            [_need()],
            [external],
        )


def test_global_semantic_filter_assesses_a_deep_candidate_set_atomically() -> None:
    records = [
        _record(
            f"common:{index}",
            f"Pre-press holding observation {index} for apple juice yield.",
        )
        for index in range(1, 37)
    ]

    class DynamicClient:
        def __init__(self) -> None:
            self.calls = 0
            self.maximum_output_tokens = 0

        def chat(self, messages, **options):
            self.calls += 1
            self.maximum_output_tokens = options["max_output_tokens"]
            request = json.loads(messages[1]["content"])
            return GenerationResponse(
                model="argo-semantic-test",
                content=json.dumps(
                    {
                        "decisions": [
                            {
                                "candidate_id": candidate["candidate_id"],
                                "relevance": "direct",
                                "supported_need_ids": ["v1"],
                                "rationale": "Direct matrix, process and outcome.",
                            }
                            for candidate in request["candidates"]
                        ]
                    }
                ),
                done_reason="stop",
                metrics=GenerationMetrics(
                    total_duration_seconds=1,
                    load_duration_seconds=0,
                    prompt_eval_count=100,
                    prompt_eval_duration_seconds=0.5,
                    eval_count=100,
                    eval_duration_seconds=0.5,
                ),
            )

    client = DynamicClient()
    result = ArgoGlobalSemanticEvidenceFilter(client).filter_records(
        "Quels effets le cuvage a-t-il sur le rendement ?",
        [_need()],
        records,
    )

    assert client.calls == 4
    assert client.maximum_output_tokens == 1_880
    assert result.used_fallback is False
    assert result.selected_candidate_ids == [record.record_id for record in records]
