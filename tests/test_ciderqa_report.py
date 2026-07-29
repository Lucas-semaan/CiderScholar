from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplitDataset
from app.evaluation.ciderqa_metrics import (
    CiderQACitationAssessment,
    CiderQAClaimAssessment,
    CiderQAInferenceResult,
)
from app.evaluation.ciderqa_report import (
    CiderQARunContext,
    SignedCiderQAReport,
    build_signed_ciderqa_report,
    verify_ciderqa_report,
    write_ciderqa_report,
)


def _dataset_and_results() -> tuple[CiderQASplitDataset, list[CiderQAInferenceResult]]:
    answerable = CiderQAQuestion.model_validate(
        {
            "schema_version": 1,
            "id": "ciderqa-report-answer",
            "family_id": "family-report-answer",
            "split": "validation",
            "language": "fr",
            "task": "direct",
            "question": "Quel résultat ?",
            "answerable": True,
            "expected_answer": "La teneur diminue.",
            "expected_claims": ["La teneur diminue."],
            "reference_evidence": [
                {
                    "id": "evidence-report",
                    "notice_id": "notice-1",
                    "article_id": "article-1",
                    "fragment_id": "fragment-1",
                    "article_sha256": "a" * 64,
                    "kind": "body",
                    "page_start": 2,
                    "page_end": 2,
                    "excerpt": "La teneur diminue.",
                }
            ],
        }
    )
    unanswerable = CiderQAQuestion.model_validate(
        {
            "schema_version": 1,
            "id": "ciderqa-report-abstain",
            "family_id": "family-report-abstain",
            "split": "validation",
            "language": "en",
            "task": "abstention",
            "question": "What cannot be answered?",
            "answerable": False,
        }
    )
    result = CiderQAInferenceResult(
        question_id=answerable.id,
        answered=True,
        insufficiency_score=0.1,
        ranked_notice_ids=["notice-1"],
        ranked_article_ids=["article-1"],
        ranked_fragment_ids=["fragment-1"],
        claims=[
            CiderQAClaimAssessment(
                text="La teneur diminue.",
                factually_correct=True,
                expected_claim_indexes=[0],
                citations=[
                    CiderQACitationAssessment(
                        evidence_id="evidence-report", entailed=True, page_exact=True
                    )
                ],
            )
        ],
    )
    abstention = CiderQAInferenceResult(
        question_id=unanswerable.id,
        answered=False,
        insufficiency_score=0.9,
    )
    return (
        CiderQASplitDataset(
            schema_version=1,
            split="validation",
            questions=[answerable, unanswerable],
        ),
        [result, abstention],
    )


def _context(*, argo_used: int = 0, authorized: bool = False) -> CiderQARunContext:
    started = datetime(2026, 7, 22, 12, tzinfo=UTC)
    return CiderQARunContext(
        schema_version=1,
        split="validation",
        mode="full_text",
        corpus_sha256="b" * 64,
        code_revision="abcdef123456",
        model_versions={"embedding": "e5-v1", "generator": "argo-v1"},
        prompt_sha256="c" * 64,
        parameters={"top_k": 20, "threshold": 0.5},
        seeds={"bootstrap": 1729},
        started_at=started,
        completed_at=started + timedelta(seconds=5),
        duration_seconds=5,
        peak_process_rss_gb=1.2,
        peak_system_used_gb=8.4,
        argo_authorized=authorized,
        argo_request_limit=1 if authorized else 0,
        argo_requests_used=argo_used,
        argo_prompt_tokens=100 if argo_used else 0,
        argo_completion_tokens=20 if argo_used else 0,
        argo_cost_eur=0.01 if argo_used else 0,
    )


def test_signed_report_records_reproducibility_and_detects_changes(tmp_path) -> None:
    dataset, results = _dataset_and_results()
    report = build_signed_ciderqa_report(
        dataset,
        results,
        _context(),
        dataset_version="1.0.0",
        dataset_sha256="d" * 64,
        created_at=datetime(2026, 7, 22, 13, tzinfo=UTC),
    )

    assert verify_ciderqa_report(report)
    assert report.context.parameters == {"top_k": 20, "threshold": 0.5}
    assert report.context.argo_requests_used == 0
    written = write_ciderqa_report(report, tmp_path / "report.json")
    reloaded = SignedCiderQAReport.model_validate_json(written.read_text(encoding="utf-8"))
    assert verify_ciderqa_report(reloaded)

    payload = reloaded.model_dump(mode="json")
    payload["context"]["corpus_sha256"] = "e" * 64
    changed = SignedCiderQAReport.model_validate(payload)
    assert not verify_ciderqa_report(changed)


def test_run_context_rejects_implicit_or_over_budget_argo_usage() -> None:
    with pytest.raises(ValidationError, match="explicit authorization"):
        _context(argo_used=1, authorized=False)

    payload = _context(argo_used=1, authorized=True).model_dump(mode="json")
    payload["argo_requests_used"] = 2
    with pytest.raises(ValidationError, match="request budget"):
        CiderQARunContext.model_validate(payload)


def test_result_file_has_no_hidden_fields() -> None:
    _, results = _dataset_and_results()
    serialized = json.loads(results[0].model_dump_json())

    assert "expected_answer" not in serialized
    assert "reference_evidence" not in serialized
