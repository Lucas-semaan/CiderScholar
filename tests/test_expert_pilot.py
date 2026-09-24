from uuid import uuid4

import pytest

from app.evaluation.expert_memory import (
    EvaluationMetric,
    ExpertEvaluationManifest,
    ExpertEvaluationReport,
)
from app.evaluation.expert_pilot import (
    PILOT_MEASUREMENT_PROTOCOL_SHA256,
    ExpertPilotAttestation,
    ExpertPilotAudit,
    ExpertPilotError,
    ExpertPilotObservation,
    attest_expert_pilot,
    audit_expert_pilot,
    build_expert_pilot_plan,
    build_pilot_metrics,
)
from app.evaluation.expert_pilot_store import (
    ExpertPilotStoreError,
    attest_pilot,
    audit_pilot,
    create_pilot_plan,
    get_pilot,
    record_pilot_observation,
)


def test_pilot_plan_audit_and_attestation_are_durable(settings, expert_package_payload) -> None:
    from tests.test_expert_review import _prepared_candidate

    database, _candidate_id, _release, evaluation_id, report = _prepared_candidate(
        settings, expert_package_payload
    )
    with database.transaction() as connection:
        evaluation = connection.execute(
            "SELECT manifest_json FROM expert_evaluations WHERE id = ?",
            (str(evaluation_id),),
        ).fetchone()
        manifest = ExpertEvaluationManifest.model_validate_json(evaluation["manifest_json"])
        manifest = manifest.model_copy(update={"split": "validation"})
        report = report.model_copy(
            update={
                "manifest_sha256": manifest.manifest_sha256,
                "metrics": (
                    EvaluationMetric(
                        name="execution_success_rate", base=1.0, candidate=1.0, delta=0.0
                    ),
                    EvaluationMetric(
                        name="control_execution_success_rate",
                        base=1.0,
                        candidate=1.0,
                        delta=0.0,
                    ),
                ),
            }
        )
        connection.execute(
            """
            UPDATE expert_evaluations
            SET manifest_json = ?, manifest_sha256 = ?, report_json = ?, report_sha256 = ?
            WHERE id = ?
            """,
            (
                manifest.model_dump_json(),
                manifest.manifest_sha256,
                report.model_dump_json(),
                report.report_sha256,
                str(evaluation_id),
            ),
        )
    pilot_id = uuid4()
    planned, created = create_pilot_plan(database, evaluation_id, pilot_id=pilot_id)
    replay, replayed = create_pilot_plan(database, evaluation_id, pilot_id=pilot_id)
    assert created is True
    assert replayed is False
    assert replay == planned

    audited = audit_pilot(database, pilot_id)
    assert audited["state"] == "ready"
    assert audited["audit"] is not None
    observation = ExpertPilotObservation(
        observation_id=uuid4(),
        pilot_id=pilot_id,
        case_sha256=planned["plan"]["question_hashes"][0],
        protocol_sha256=planned["plan"]["measurement_protocol_sha256"],
        expert_time_seconds=42,
        diagnosis_human_corrected=True,
        useful_effect="useful",
        false_gain=False,
        rollback_count=0,
        prompt_tokens=100,
        completion_tokens=50,
        recorded_by="expert-1",
    )
    observed, observation_created = record_pilot_observation(database, pilot_id, observation)
    replayed_observed, replayed_observation = record_pilot_observation(
        database, pilot_id, observation
    )
    assert observation_created is True
    assert replayed_observation is False
    assert replayed_observed == observed
    assert observed["metrics"]["observation_count"] == 1
    with pytest.raises(ExpertPilotStoreError, match="pilot_case_not_in_plan"):
        record_pilot_observation(
            database,
            pilot_id,
            observation.model_copy(update={"observation_id": uuid4(), "case_sha256": "9" * 64}),
        )
    audit = ExpertPilotAudit.model_validate(audited["audit"])
    attestation = attest_expert_pilot(
        audit,
        attestation_id=uuid4(),
        reviewer_label="panel-1",
        external_reference="review-2026-09",
        decision="accept",
        reason="Contrôles et rapport vérifiés.",
    )
    stored, stored_created = attest_pilot(database, pilot_id, attestation)
    replayed_stored, replayed_created = attest_pilot(database, pilot_id, attestation)
    assert stored_created is True
    assert replayed_created is False
    assert replayed_stored == stored
    assert get_pilot(database, pilot_id)["state"] == "attested"
    assert replayed_stored["metrics"]["observation_count"] == 1
    assert stored["attestation"]["activation_allowed"] is False


def test_pilot_store_rejects_acceptance_without_audit(settings, expert_package_payload) -> None:
    from tests.test_expert_review import _prepared_candidate

    database, _candidate_id, _release, evaluation_id, _report = _prepared_candidate(
        settings, expert_package_payload
    )
    with database.transaction() as connection:
        evaluation = connection.execute(
            "SELECT manifest_json FROM expert_evaluations WHERE id = ?",
            (str(evaluation_id),),
        ).fetchone()
        manifest = ExpertEvaluationManifest.model_validate_json(evaluation["manifest_json"])
        connection.execute(
            "UPDATE expert_evaluations SET manifest_json = ?, manifest_sha256 = ? WHERE id = ?",
            (
                manifest.model_copy(update={"split": "validation"}).model_dump_json(),
                manifest.model_copy(update={"split": "validation"}).manifest_sha256,
                str(evaluation_id),
            ),
        )
    pilot_id = uuid4()
    create_pilot_plan(database, evaluation_id, pilot_id=pilot_id)
    with pytest.raises(ExpertPilotStoreError, match="pilot_audit_missing"):
        attest_pilot(
            database,
            pilot_id,
            ExpertPilotAttestation(
                attestation_id=uuid4(),
                pilot_id=pilot_id,
                audit_sha256="a" * 64,
                reviewer_label="panel-1",
                external_reference="review-1",
                decision="accept",
                reason="Audit absent.",
            ),
        )


def _manifest() -> ExpertEvaluationManifest:
    return ExpertEvaluationManifest(
        candidate_id=uuid4(),
        base_release_id=uuid4(),
        candidate_release_id=uuid4(),
        corpus_snapshot_sha256="a" * 64,
        configuration_sha256="b" * 64,
        code_revision="pilot-test",
        split="validation",
        question_hashes=("c" * 64,),
        control_question_hashes=("d" * 64, "e" * 64),
        mode="abstract_only",
        max_llm_requests=0,
        argo_authorized=False,
    )


def _passed_report(manifest: ExpertEvaluationManifest, evaluation_id):
    return ExpertEvaluationReport(
        evaluation_id=evaluation_id,
        manifest_sha256=manifest.manifest_sha256,
        base_report_sha256="f" * 64,
        candidate_report_sha256="0" * 64,
        state="passed",
        case_count=3,
        control_case_count=2,
        deterministic_validators_passed=True,
        metrics=(
            EvaluationMetric(name="execution_success_rate", base=1.0, candidate=1.0, delta=0.0),
            EvaluationMetric(
                name="control_execution_success_rate", base=1.0, candidate=1.0, delta=0.0
            ),
        ),
    )


def test_pilot_metrics_use_bounded_nearest_rank_percentiles() -> None:
    pilot_id = uuid4()
    observations = tuple(
        ExpertPilotObservation(
            observation_id=uuid4(),
            pilot_id=pilot_id,
            case_sha256=f"{index + 1:064x}",
            protocol_sha256=PILOT_MEASUREMENT_PROTOCOL_SHA256,
            expert_time_seconds=index + 1,
            diagnosis_human_corrected=index % 2 == 0,
            useful_effect="useful" if index < 6 else "neutral",
            false_gain=index == 9,
            rollback_count=1 if index == 9 else 0,
            prompt_tokens=10 + index,
            completion_tokens=20 + index,
            recorded_by="expert-1",
        )
        for index in range(10)
    )
    metrics = build_pilot_metrics(
        pilot_id, observations, protocol_sha256=PILOT_MEASUREMENT_PROTOCOL_SHA256
    )
    assert metrics.complete is True
    assert metrics.p50_expert_time_seconds == 5
    assert metrics.p95_expert_time_seconds == 10
    assert metrics.p50_total_tokens == 38
    assert metrics.p95_total_tokens == 48
    assert metrics.diagnosis_human_corrected_count == 5
    assert metrics.useful_effect_count == 6
    assert metrics.false_gain_count == 1
    assert metrics.rollback_count == 1


def test_shadow_pilot_can_be_ready_without_authorizing_activation() -> None:
    manifest = _manifest()
    evaluation_id = uuid4()
    plan = build_expert_pilot_plan(
        manifest,
        pilot_id=uuid4(),
        evaluation_id=evaluation_id,
        candidate_sha256="1" * 64,
    )
    audit = audit_expert_pilot(
        plan,
        candidate_state="awaiting_review",
        evaluation_id=evaluation_id,
        evaluation_report=_passed_report(manifest, evaluation_id),
    )

    assert audit.state == "ready"
    assert audit.activation_allowed is False
    assert audit.human_approval_required is True
    assert len(audit.audit_sha256) == 64


def test_shadow_pilot_blocks_inconclusive_report_and_wrong_candidate_state() -> None:
    manifest = _manifest()
    evaluation_id = uuid4()
    plan = build_expert_pilot_plan(
        manifest,
        pilot_id=uuid4(),
        evaluation_id=evaluation_id,
        candidate_sha256="1" * 64,
    )
    report = _passed_report(manifest, evaluation_id).model_copy(update={"state": "inconclusive"})
    audit = audit_expert_pilot(
        plan,
        candidate_state="structurally_valid",
        evaluation_id=evaluation_id,
        evaluation_report=report,
    )

    assert audit.state == "blocked"
    assert "evaluation_not_passed" in audit.blockers
    assert "candidate_not_awaiting_review" in audit.blockers


def test_external_attestation_binds_to_audit_without_activation_authority() -> None:
    manifest = _manifest()
    evaluation_id = uuid4()
    plan = build_expert_pilot_plan(
        manifest,
        pilot_id=uuid4(),
        evaluation_id=evaluation_id,
        candidate_sha256="1" * 64,
    )
    audit = audit_expert_pilot(
        plan,
        candidate_state="awaiting_review",
        evaluation_id=evaluation_id,
        evaluation_report=_passed_report(manifest, evaluation_id),
    )
    attestation = attest_expert_pilot(
        audit,
        attestation_id=uuid4(),
        reviewer_label="external-panel-1",
        external_reference="ticket-2026-001",
        decision="accept",
        reason="Le pilote est acceptable pour la phase suivante.",
    )
    assert attestation.audit_sha256 == audit.audit_sha256
    assert attestation.activation_allowed is False
    assert len(attestation.attestation_sha256) == 64

    blocked = audit.model_copy(update={"state": "blocked"})
    try:
        attest_expert_pilot(
            blocked,
            attestation_id=uuid4(),
            reviewer_label="external-panel-1",
            external_reference="ticket-2026-002",
            decision="accept",
            reason="Ce pilote est incomplet.",
        )
    except ExpertPilotError as error:
        assert str(error) == "blocked pilot cannot be externally accepted"
    else:
        raise AssertionError("a blocked pilot must not be externally accepted")
