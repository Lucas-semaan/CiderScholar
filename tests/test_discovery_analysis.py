from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.discovery.analysis import (
    AnalysisEnvironmentManifest,
    AnalysisIsolationUnavailableError,
    AnalysisTrajectory,
    DiscoveryCycleApproval,
    GeneratedCodeReview,
    GroundTruthCase,
    GroundTruthMetric,
    IsolatedExecutionResult,
    analyze_fermentation,
    analyze_polyphenols,
    analyze_sensory,
    analyze_volatiles,
    build_analysis_record,
    build_next_cycle_hypothesis,
    build_trajectory_consensus,
    execute_reviewed_analysis,
    require_generated_code_approval,
    score_benchmark,
    trajectory_limit,
)
from app.discovery.contracts import (
    DiscriminatingExperiment,
    HypothesisDraft,
    HypothesisPremise,
    content_hash,
)
from app.discovery.data import ExperimentalDataset, ExperimentalDatasetImporter


def _dataset(kind: str, records: list[dict]) -> ExperimentalDataset:
    return ExperimentalDataset.model_validate(
        {
            "schema_version": 1,
            "kind": kind,
            "controls": {"blank": "present", "method": "validated"},
            "records": records,
        }
    )


def test_four_deterministic_domain_workflows_produce_numeric_results() -> None:
    fermentation = analyze_fermentation(
        _dataset(
            "fermentation",
            [
                {
                    "kind": "fermentation",
                    "sample_id": "A",
                    "replicate": 1,
                    "time_hours": 0,
                    "temperature_c": 18,
                    "density_g_ml": 1.05,
                },
                {
                    "kind": "fermentation",
                    "sample_id": "A",
                    "replicate": 1,
                    "time_hours": 10,
                    "temperature_c": 18,
                    "density_g_ml": 1.0,
                },
            ],
        )
    )
    volatiles = analyze_volatiles(
        _dataset(
            "volatiles",
            [
                {
                    "kind": "volatiles",
                    "sample_id": "A",
                    "replicate": 1,
                    "compound": "ester",
                    "concentration": 1000,
                    "unit": "ug/L",
                }
            ],
        )
    )
    polyphenols = analyze_polyphenols(
        _dataset(
            "polyphenols",
            [
                {
                    "kind": "polyphenols",
                    "sample_id": "A",
                    "replicate": 1,
                    "analyte": "tannin",
                    "concentration_mg_l": 12,
                }
            ],
        )
    )
    sensory = analyze_sensory(
        _dataset(
            "sensory",
            [
                {
                    "kind": "sensory",
                    "sample_id": "A",
                    "assessor_pseudonym": "panel-1",
                    "replicate": 1,
                    "attribute": "fruité",
                    "score": 5,
                    "scale_min": 0,
                    "scale_max": 10,
                }
            ],
        )
    )

    assert fermentation.metrics["mean_density_rate_g_ml_per_hour"] == pytest.approx(-0.005)
    assert volatiles.metrics["mean_mg_l:ester"] == 1
    assert polyphenols.metrics["mean_mg_l:tannin"] == 12
    assert sensory.metrics["mean_normalized:fruité"] == 0.5
    assert sensory.warnings


def test_generated_code_is_blocked_until_exact_human_review() -> None:
    code = "print('analysis')"
    environment = AnalysisEnvironmentManifest(
        python_version="3.12.10",
        packages={"numpy": "2.2.1"},
        network_enabled=False,
        cpu_limit=2,
        memory_limit_gb=5,
        timeout_seconds=60,
    )
    rejected = GeneratedCodeReview(
        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        dependencies=environment.packages,
        input_files=["data.json"],
        output_files=["result.json"],
        environment=environment,
        decision="reject",
        reviewer_reference="expert-1",
        reviewed_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    with pytest.raises(PermissionError, match="human approval"):
        require_generated_code_approval(rejected, code)
    approved = rejected.model_copy(update={"decision": "approve"})
    require_generated_code_approval(approved, code)
    with pytest.raises(ValueError, match="code hash"):
        require_generated_code_approval(approved, code + " ")

    with pytest.raises(AnalysisIsolationUnavailableError, match="no attested"):
        execute_reviewed_analysis(code=code, review=approved)

    class FakeIsolatedExecutor:
        def execute(self, *, code: str, review: GeneratedCodeReview) -> IsolatedExecutionResult:
            return IsolatedExecutionResult(
                backend="test-sandbox",
                isolation_reference="sandbox-run-1",
                code_sha256=hashlib.sha256(code.encode()).hexdigest(),
                environment_sha256=content_hash(review.environment.model_dump(mode="json")),
                output_sha256={"result.json": "a" * 64},
                duration_seconds=0.1,
                peak_memory_gb=0.2,
            )

    receipt = execute_reviewed_analysis(
        code=code,
        review=approved,
        executor=FakeIsolatedExecutor(),
    )
    assert receipt.output_sha256 == {"result.json": "a" * 64}


def test_trajectory_consensus_exposes_variability_failures_and_parameters() -> None:
    assert trajectory_limit("8gb", 10) == 2
    assert trajectory_limit("16gb", 3) == 3
    consensus = build_trajectory_consensus(
        [
            AnalysisTrajectory(index=1, parameters={"window": 3}, result={"slope": 1.0}),
            AnalysisTrajectory(index=2, parameters={"window": 5}, result={"slope": 3.0}),
            AnalysisTrajectory(index=3, parameters={"window": 7}, error="fit failed"),
        ]
    )
    assert consensus.metric_means["slope"] == 2
    assert consensus.disagreements["slope"] == 1
    assert consensus.failed_trajectories == [3]
    assert len(consensus.parameter_choices) == 3


def test_benchmark_counts_numerically_wrong_runs_as_failures() -> None:
    dataset = _dataset(
        "polyphenols",
        [
            {
                "kind": "polyphenols",
                "sample_id": "A",
                "replicate": 1,
                "analyte": "tannin",
                "concentration_mg_l": 12,
            }
        ],
    )
    result = analyze_polyphenols(dataset)
    truth = [
        GroundTruthCase(
            id="case-1",
            dataset_sha256="a" * 64,
            workflow="polyphenols",
            metrics={
                "mean_mg_l:tannin": GroundTruthMetric(
                    expected=10,
                    absolute_tolerance=0.1,
                )
            },
        )
    ]
    report = score_benchmark(truth, {"case-1": (result, True, 0.01, None)})
    assert report.exact_accuracy == 0
    assert report.failure_rate == 1
    assert report.all_cases_passed is False


def test_raw_dataset_import_and_analysis_lineage_are_content_addressed(settings, tmp_path) -> None:
    raw = tmp_path / "polyphenols.json"
    raw.write_text(
        _dataset(
            "polyphenols",
            [
                {
                    "kind": "polyphenols",
                    "sample_id": "A",
                    "replicate": 1,
                    "analyte": "tannin",
                    "concentration_mg_l": 12,
                }
            ],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    importer = ExperimentalDatasetImporter(
        settings.paths.database_path,
        settings.paths.data_dir / "experimental",
    )
    manifest = importer.import_file(
        raw,
        source_reference="étude locale non sensible",
        imported_by="scientist-1",
    )
    stored = _dataset(
        "polyphenols",
        [
            {
                "kind": "polyphenols",
                "sample_id": "A",
                "replicate": 1,
                "analyte": "tannin",
                "concentration_mg_l": 12,
            }
        ],
    )
    result = analyze_polyphenols(stored)
    code = "validated_polyphenol_workflow_v1"
    environment = AnalysisEnvironmentManifest(
        python_version="3.12.10",
        packages={"pydantic": "2.13.0"},
        network_enabled=False,
        cpu_limit=1,
        memory_limit_gb=5,
        timeout_seconds=60,
    )
    review = GeneratedCodeReview(
        code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        dependencies=environment.packages,
        input_files=[manifest.stored_path],
        output_files=["result.json"],
        environment=environment,
        decision="approve",
        reviewer_reference="expert-1",
        reviewed_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    record = build_analysis_record(
        dataset_id=manifest.id,
        notebook=b"notebook",
        code=code,
        parameters={"workflow": "1.0.0"},
        input_sha256=manifest.raw_sha256,
        result=result,
        review=review,
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert Path(manifest.stored_path).read_bytes() == raw.read_bytes()
    assert manifest.raw_sha256 == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert record.input_sha256 == manifest.raw_sha256
    assert record.output_sha256 != record.input_sha256


def test_next_cycle_requires_human_approval_and_both_provenances() -> None:
    previous = uuid4()
    analysis = uuid4()
    dataset = uuid4()
    approval = DiscoveryCycleApproval(
        previous_hypothesis_id=previous,
        analysis_id=analysis,
        decision="approve_next",
        expert_reference="expert-1",
        literature_evidence_ids=["claim-1"],
        experimental_dataset_ids=[dataset],
    )
    draft = HypothesisDraft(
        premises=[
            HypothesisPremise(
                statement="La littérature et les résultats expérimentaux convergent.",
                evidence_ids=["claim-1"],
            )
        ],
        contradictions=["Une condition expérimentale produit un résultat opposé."],
        uncertainties=["La généralisation à un autre cultivar reste inconnue."],
        explicit_gaps=["Une réplication indépendante reste nécessaire."],
        testable_prediction="La tendance se reproduira dans un lot indépendant comparable.",
        discriminating_experiment=DiscriminatingExperiment(
            principle="Comparer des lots indépendants selon un plan validé par un expert.",
            discriminating_outcome=(
                "La reproduction ou l’absence de tendance départage les hypothèses."
            ),
            safety_review_required=True,
            executable_protocol=False,
        ),
    )
    card = build_next_cycle_hypothesis(
        approval=approval,
        question="La tendance conjointe se reproduit-elle dans un nouveau lot ?",
        draft=draft,
        validated_literature_evidence_ids={"claim-1"},
        corpus_sha256="a" * 64,
        model_sha256="b" * 64,
        prompt_sha256="c" * 64,
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert card.parent_hypothesis_id == previous
    assert card.source_analysis_ids == [analysis]
    assert card.experimental_dataset_ids == [dataset]
    with pytest.raises(PermissionError, match="stopped"):
        build_next_cycle_hypothesis(
            approval=approval.model_copy(update={"decision": "stop"}),
            question=card.question,
            draft=draft,
            validated_literature_evidence_ids={"claim-1"},
            corpus_sha256="a" * 64,
            model_sha256="b" * 64,
            prompt_sha256="c" * 64,
            created_at=datetime(2026, 7, 27, tzinfo=UTC),
        )
