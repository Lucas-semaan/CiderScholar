from __future__ import annotations

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.database.sqlite import Database
from app.evaluation.expert_memory import (
    ExpertEvaluationCase,
    ExpertEvaluationError,
    ExpertEvaluationEvidenceError,
    ExpertEvaluationManifest,
    ExpertEvaluationReport,
    build_campaign_comparison_report,
    build_campaign_pair_specs,
    plan_evaluation,
)
from app.jobs.contracts import ExpertMemoryPin
from app.jobs.repository import JobRepository
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository


def _manifest(split: str = "development") -> ExpertEvaluationManifest:
    digest = "a" * 64
    return ExpertEvaluationManifest(
        candidate_id=uuid4(),
        base_release_id=uuid4(),
        candidate_release_id=uuid4(),
        corpus_snapshot_sha256=digest,
        configuration_sha256="b" * 64,
        code_revision="test-revision",
        split=split,
        question_hashes=("c" * 64,),
        control_question_hashes=("d" * 64, "e" * 64),
        mode="abstract_only",
        max_llm_requests=0,
        argo_authorized=False,
    )


def test_evaluation_manifest_is_label_free_bounded_and_hashable() -> None:
    manifest = _manifest()
    assert manifest.complete_controls() == manifest
    assert len(manifest.manifest_sha256) == 64
    assert "final_test" not in manifest.model_dump(mode="json").values()

    with pytest.raises(ValidationError):
        _manifest("final_test")

    with pytest.raises(ValueError, match="two independent controls"):
        manifest.model_copy(update={"control_question_hashes": ("d" * 64,)}).complete_controls()


def test_evaluation_plan_requires_a_persisted_candidate(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()

    with pytest.raises(ExpertEvaluationError, match="candidate_not_found"):
        plan_evaluation(database, _manifest())


def test_evaluation_job_can_pin_a_candidate_release(expert_package_payload, settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    release = KnowledgeRepository(database).import_candidate(
        KnowledgePackage.model_validate(expert_package_payload)
    )
    pin = ExpertMemoryPin(
        mode="shadow",
        release_id=release.id,
        release_sha256=release.package_sha256,
    )

    enqueued = JobRepository(settings.paths.database_path).enqueue_evaluation_question(
        run_id="candidate-pin-run",
        question_id="Q1",
        profile="p0",
        message="Question épinglée",
        client_request_id=uuid4(),
        expert_memory_pin=pin,
    )

    assert enqueued.job.payload.expert_memory_pin == pin


def test_campaign_pair_specs_match_manifest_hashes_and_pin_both_releases(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    base_release_id = uuid4()
    candidate_release_id = uuid4()
    messages = ("Question principale", "Question secondaire", "Contrôle reformulé", "Cas frontière")
    hashes = tuple(sha256(message.encode("utf-8")).hexdigest() for message in messages)
    manifest = ExpertEvaluationManifest(
        candidate_id=uuid4(),
        base_release_id=base_release_id,
        candidate_release_id=candidate_release_id,
        corpus_snapshot_sha256="a" * 64,
        configuration_sha256="b" * 64,
        code_revision="test-revision",
        split="development",
        question_hashes=hashes[:2],
        control_question_hashes=hashes[2:],
        mode="abstract_only",
        max_llm_requests=0,
        argo_authorized=False,
    )
    with database.transaction() as connection:
        for release_id, package_sha256 in (
            (base_release_id, "c" * 64),
            (candidate_release_id, "d" * 64),
        ):
            connection.execute(
                """
                INSERT INTO expert_releases(
                    id, package_sha256, schema_version, app_min_version, created_at,
                    manifest_json, state
                ) VALUES (?, ?, 1, '0.2.11', CURRENT_TIMESTAMP, '{}', 'candidate')
                """,
                (str(release_id), package_sha256),
            )

    cases = tuple(
        ExpertEvaluationCase(question_id=f"Q{index}", profile="p0", message=message)
        for index, message in enumerate(messages, start=1)
    )
    base, candidate = build_campaign_pair_specs(database, manifest, cases, run_id_prefix="pair")

    assert base.run_id == "pair-base"
    assert candidate.run_id == "pair-candidate"
    assert [cell.message for cell in base.cells] == [cell.message for cell in candidate.cells]
    assert base.expert_memory_pin.release_id == base_release_id
    assert candidate.expert_memory_pin.release_id == candidate_release_id


def test_evaluation_report_requires_deterministic_validation_for_success() -> None:
    report = ExpertEvaluationReport(
        evaluation_id=uuid4(),
        manifest_sha256="a" * 64,
        base_report_sha256="b" * 64,
        candidate_report_sha256="c" * 64,
        state="passed",
        case_count=3,
        control_case_count=2,
        deterministic_validators_passed=True,
    )
    assert report.complete_controls() == report
    assert len(report.report_sha256) == 64
    with pytest.raises(ValueError, match="deterministic validators"):
        report.model_copy(update={"deterministic_validators_passed": False}).complete_controls()


def test_campaign_comparison_is_bounded_and_stays_inconclusive_without_labels(tmp_path) -> None:
    manifest = _manifest()
    states = {
        "status": "completed",
        "run_id": "campaign",
        "cells": {
            "p0:Q": {"question_sha256": "c" * 64, "state": "succeeded"},
            "p0:C1": {"question_sha256": "d" * 64, "state": "succeeded"},
            "p0:C2": {"question_sha256": "e" * 64, "state": "succeeded"},
        },
    }
    base_path = tmp_path / "base" / "state.json"
    candidate_path = tmp_path / "candidate" / "state.json"
    base_path.parent.mkdir()
    candidate_path.parent.mkdir()
    base_path.write_text(json.dumps(states), encoding="utf-8")
    candidate_path.write_text(json.dumps({**states, "run_id": "candidate"}), encoding="utf-8")

    report = build_campaign_comparison_report(
        manifest,
        evaluation_id=uuid4(),
        base_state_path=base_path,
        candidate_state_path=candidate_path,
    )

    assert report.state == "inconclusive"
    assert report.deterministic_validators_passed is True
    assert report.case_count == 3
    assert report.control_case_count == 2
    assert {metric.name for metric in report.metrics} == {
        "execution_success_rate",
        "control_execution_success_rate",
    }


def test_campaign_comparison_rejects_incomplete_question_coverage(tmp_path) -> None:
    manifest = _manifest()
    state = {
        "status": "completed",
        "run_id": "campaign",
        "cells": {"p0:Q": {"question_sha256": "c" * 64, "state": "succeeded"}},
    }
    base_path = tmp_path / "base.json"
    candidate_path = tmp_path / "candidate.json"
    base_path.write_text(json.dumps(state), encoding="utf-8")
    candidate_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ExpertEvaluationEvidenceError, match="coverage is incomplete"):
        build_campaign_comparison_report(
            manifest,
            evaluation_id=uuid4(),
            base_state_path=base_path,
            candidate_state_path=candidate_path,
        )
