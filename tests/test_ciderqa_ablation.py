from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.deep_research.cache import combined_corpus_fingerprint
from app.deep_research.promotion import (
    activation_bundle_path,
    assess_deep_research_promotion,
    build_activation_bundle,
    deep_research_availability,
    verify_activation_bundle,
    write_activation_bundle,
)
from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplitDataset
from app.evaluation.ciderqa_ablation import (
    ABLATION_VARIANTS,
    build_ablation_plan,
    build_ablation_report,
    fixed_ablation_configurations,
    verify_ablation_plan,
    verify_ablation_report,
)
from app.evaluation.ciderqa_baselines import (
    build_baseline_comparison,
    verify_baseline_comparison,
)
from app.evaluation.ciderqa_metrics import (
    CiderQACitationAssessment,
    CiderQAClaimAssessment,
    CiderQAInferenceResult,
)
from app.evaluation.ciderqa_report import CiderQARunContext, build_signed_ciderqa_report


def _dataset() -> CiderQASplitDataset:
    return CiderQASplitDataset(
        schema_version=1,
        split="validation",
        questions=[
            CiderQAQuestion.model_validate(
                {
                    "schema_version": 1,
                    "id": "ciderqa-answerable",
                    "family_id": "family-answerable",
                    "split": "validation",
                    "language": "fr",
                    "task": "direct",
                    "question": "Résultat ?",
                    "answerable": True,
                    "expected_answer": "La teneur baisse.",
                    "expected_claims": ["La teneur baisse."],
                    "reference_evidence": [
                        {
                            "id": "evidence-answerable",
                            "notice_id": "notice-1",
                            "article_id": "article-1",
                            "fragment_id": "fragment-1",
                            "article_sha256": "a" * 64,
                            "kind": "body",
                            "page_start": 2,
                            "page_end": 2,
                            "excerpt": "La teneur baisse.",
                        }
                    ],
                }
            ),
            CiderQAQuestion.model_validate(
                {
                    "schema_version": 1,
                    "id": "ciderqa-unanswerable",
                    "family_id": "family-unanswerable",
                    "split": "validation",
                    "language": "en",
                    "task": "abstention",
                    "question": "Unknown?",
                    "answerable": False,
                }
            ),
        ],
    )


def _results() -> list[CiderQAInferenceResult]:
    return [
        CiderQAInferenceResult(
            question_id="ciderqa-answerable",
            answered=True,
            insufficiency_score=0.1,
            ranked_notice_ids=["notice-1"],
            ranked_article_ids=["article-1"],
            ranked_fragment_ids=["fragment-1"],
            claims=[
                CiderQAClaimAssessment(
                    text="La teneur baisse.",
                    factually_correct=True,
                    expected_claim_indexes=[0],
                    citations=[
                        CiderQACitationAssessment(
                            evidence_id="evidence-answerable",
                            entailed=True,
                            page_exact=True,
                        )
                    ],
                )
            ],
        ),
        CiderQAInferenceResult(
            question_id="ciderqa-unanswerable",
            answered=False,
            insufficiency_score=0.9,
        ),
    ]


def _plan(corpus_sha256: str = "c" * 64):
    return build_ablation_plan(
        dataset_version="1.0.0",
        dataset_sha256="d" * 64,
        split="validation",
        mode="full_text",
        corpus_sha256=corpus_sha256,
        code_revision="abcdef123456",
        model_versions={"embedding": "e5", "reranker": "cross-encoder", "generator": "argo"},
        seeds={"bootstrap": 1729},
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
    )


def _reports(plan):
    started = datetime(2026, 7, 27, 10, tzinfo=UTC)
    reports = {}
    for index, configuration in enumerate(plan.configurations):
        context = CiderQARunContext(
            schema_version=1,
            split="validation",
            mode="full_text",
            corpus_sha256=plan.corpus_sha256,
            code_revision="abcdef123456",
            model_versions=plan.model_versions,
            prompt_sha256=f"{index:x}".zfill(64),
            parameters={
                "ablation_plan_sha256": plan.plan_sha256,
                **configuration.signed_parameters,
            },
            seeds=plan.seeds,
            started_at=started,
            completed_at=started + timedelta(seconds=10 + index),
            duration_seconds=10 + index,
            peak_process_rss_gb=1.0 + index / 10,
            peak_system_used_gb=4.0 + index / 10,
        )
        reports[configuration.variant] = build_signed_ciderqa_report(
            _dataset(),
            _results(),
            context,
            dataset_version=plan.dataset_version,
            dataset_sha256=plan.dataset_sha256,
            created_at=started + timedelta(minutes=index),
        )
    return reports


def test_fixed_matrix_has_one_stage_per_candidate_and_signed_comparison() -> None:
    plan = _plan()
    configurations = fixed_ablation_configurations()

    assert verify_ablation_plan(plan)
    assert [item.variant for item in configurations] == list(ABLATION_VARIANTS)
    assert (
        sum(
            sum(
                (
                    item.query_variants,
                    item.reranker,
                    item.contextual_summary,
                    item.iteration,
                    item.citation_traversal,
                )
            )
            for item in configurations
        )
        == 5
    )
    report = build_ablation_report(plan, _reports(plan))
    assert verify_ablation_report(report)
    assert [item.variant for item in report.comparisons] == list(ABLATION_VARIANTS)
    assert report.comparisons[0].delta_from_baseline["exactness"] == 0
    assert report.comparisons[-1].duration_delta_seconds == 5


def test_comparison_rejects_incomplete_or_inconsistent_runs() -> None:
    plan = _plan()
    reports = _reports(plan)
    reports.pop("iteration")
    with pytest.raises(ValueError, match="fixed matrix"):
        build_ablation_report(plan, reports)

    reports = _reports(plan)
    wrong = reports["reranker"]
    context = wrong.context.model_copy(
        update={
            "parameters": {
                **wrong.context.parameters,
                "drs_reranker_enabled": False,
            }
        }
    )
    reports["reranker"] = wrong.model_copy(update={"context": context})
    with pytest.raises(ValueError, match="invalid CiderQA report signature"):
        build_ablation_report(plan, reports)


def _promotion_candidate(plan, *, duration_seconds: float = 20):
    started = datetime(2026, 7, 27, 12, tzinfo=UTC)
    context = CiderQARunContext(
        schema_version=1,
        split="validation",
        mode="full_text",
        corpus_sha256=plan.corpus_sha256,
        code_revision=plan.code_revision,
        model_versions=plan.model_versions,
        prompt_sha256="f" * 64,
        parameters={
            "drs_query_variants_enabled": True,
            "drs_reranker_enabled": True,
            "drs_contextual_summary_enabled": True,
            "drs_iteration_enabled": True,
            "drs_citation_traversal_enabled": True,
            "contextual_relevance_observations_sha256": "e" * 64,
            "memory_profile": "8gb",
        },
        seeds=plan.seeds,
        started_at=started,
        completed_at=started + timedelta(seconds=duration_seconds),
        duration_seconds=duration_seconds,
        peak_process_rss_gb=1.5,
        peak_system_used_gb=4.5,
    )
    return build_signed_ciderqa_report(
        _dataset(),
        _results(),
        context,
        dataset_version=plan.dataset_version,
        dataset_sha256=plan.dataset_sha256,
        created_at=started + timedelta(minutes=1),
    )


def test_activation_requires_science_resources_and_the_installed_corpus(settings) -> None:
    corpus_sha256 = combined_corpus_fingerprint(settings)
    plan = _plan(corpus_sha256)
    source_reports = _reports(plan)
    ablation = build_ablation_report(plan, source_reports)
    candidate = _promotion_candidate(plan)
    bundle = build_activation_bundle(
        source_reports["baseline"],
        candidate,
        ablation,
        memory_profile="8gb",
        created_at=datetime(2026, 7, 27, 14, tzinfo=UTC),
    )

    assert verify_activation_bundle(bundle)
    assert deep_research_availability(settings).state == "disabled"

    payload = settings.model_dump(mode="python")
    payload["deep_research"]["enabled"] = True
    payload["memory"]["profile"] = "8gb"
    enabled = Settings.model_validate(payload)
    write_activation_bundle(bundle, activation_bundle_path(enabled))

    availability = deep_research_availability(enabled)
    assert availability.available is True
    assert availability.bundle_sha256 == bundle.bundle_sha256


def test_activation_rejects_resource_overrun() -> None:
    plan = _plan()
    source_reports = _reports(plan)
    assessment = assess_deep_research_promotion(
        source_reports["baseline"],
        _promotion_candidate(plan, duration_seconds=1801),
        build_ablation_report(plan, source_reports),
        memory_profile="8gb",
    )

    assert assessment.promoted is False
    assert any(reason.startswith("duration_seconds") for reason in assessment.failures)


def test_abstract_and_full_text_baselines_share_every_other_dimension() -> None:
    plan = _plan()
    full_text = _reports(plan)["baseline"]
    abstract_context = full_text.context.model_copy(update={"mode": "abstract_only"})
    abstract = build_signed_ciderqa_report(
        _dataset(),
        _results(),
        abstract_context,
        dataset_version=plan.dataset_version,
        dataset_sha256=plan.dataset_sha256,
        created_at=datetime(2026, 7, 27, 9, tzinfo=UTC),
    )

    comparison = build_baseline_comparison(abstract, full_text)

    assert verify_baseline_comparison(comparison)
    assert comparison.full_text_delta["citation_precision"] == 0
    incompatible = full_text.model_copy(
        update={
            "context": full_text.context.model_copy(
                update={"corpus_sha256": "f" * 64},
            )
        }
    )
    with pytest.raises(ValueError, match="source signature"):
        build_baseline_comparison(abstract, incompatible)
