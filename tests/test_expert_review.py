from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.database.sqlite import Database
from app.evaluation.expert_memory import ExpertEvaluationManifest, ExpertEvaluationReport
from app.expert_feedback.models import (
    ExpertActivationRequest,
    ExpertCorrectionCreate,
    ExpertReviewCreate,
    ExpertRollbackRequest,
)
from app.expert_feedback.repository import ExpertCorrectionRepository
from app.expert_feedback.review import (
    ExpertReviewConflictError,
    ExpertReviewError,
    activate_candidate,
    create_review,
    rollback_active_candidate,
)
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository


def _prepared_candidate(settings, expert_package_payload):
    database = Database(settings.paths.database_path)
    database.initialize()
    package = KnowledgePackage.model_validate(expert_package_payload)
    knowledge = KnowledgeRepository(database)
    base = knowledge.import_candidate(package)
    candidate_package = package.model_copy(
        update={"items": (package.items[0].model_copy(update={"body": "Version revue."}),)}
    )
    candidate_release = knowledge.import_candidate(candidate_package, base_release_id=base.id)
    conversation = database.create_chat_conversation("Revue expert")
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Réponse à revoir"
    )
    message_id = UUID(database.chat_conversation(conversation["id"])["messages"][0]["id"])
    correction, _ = ExpertCorrectionRepository(database).submit(
        message_id,
        ExpertCorrectionCreate(
            client_request_id=uuid4(),
            problem="La réponse doit être corrigée.",
            proposed_correction="Version revue.",
            scope="reusable_method",
        ),
    )
    diagnosis_id = uuid4()
    timestamp = datetime(2026, 9, 24, tzinfo=UTC).isoformat()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO expert_diagnoses(
                id, correction_id, correction_revision, payload_json,
                diagnosis_sha256, created_at
            ) VALUES (?, ?, 1, '{}', ?, ?)
            """,
            (str(diagnosis_id), str(correction["id"]), "a" * 64, timestamp),
        )
        connection.execute(
            """
            INSERT INTO expert_candidates(
                id, diagnosis_id, base_release_id, candidate_release_id,
                diff_sha256, state, attempt, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'awaiting_review', 0, ?, ?)
            """,
            (
                str(uuid4()),
                str(diagnosis_id),
                str(base.id),
                str(candidate_release.id),
                "b" * 64,
                timestamp,
                timestamp,
            ),
        )
        candidate_id = UUID(
            str(
                connection.execute(
                    "SELECT id FROM expert_candidates WHERE diagnosis_id = ?",
                    (str(diagnosis_id),),
                ).fetchone()[0]
            )
        )
        connection.execute(
            "UPDATE expert_releases SET state = 'eligible' WHERE id = ?",
            (str(base.id),),
        )
        connection.execute(
            "UPDATE expert_active_release SET release_id = ?, generation = 0 WHERE singleton = 1",
            (str(base.id),),
        )
    manifest = ExpertEvaluationManifest(
        candidate_id=candidate_id,
        base_release_id=base.id,
        candidate_release_id=candidate_release.id,
        corpus_snapshot_sha256="c" * 64,
        configuration_sha256="d" * 64,
        code_revision="review-test",
        split="development",
        question_hashes=("e" * 64,),
        control_question_hashes=("f" * 64, "0" * 64),
        mode="abstract_only",
        max_llm_requests=0,
        argo_authorized=False,
    )
    evaluation_id = uuid4()
    report = ExpertEvaluationReport(
        evaluation_id=evaluation_id,
        manifest_sha256=manifest.manifest_sha256,
        base_report_sha256="1" * 64,
        candidate_report_sha256="2" * 64,
        state="passed",
        case_count=3,
        control_case_count=2,
        deterministic_validators_passed=True,
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO expert_evaluations(
                id, candidate_id, manifest_json, manifest_sha256,
                report_json, report_sha256, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'passed', ?, ?)
            """,
            (
                str(evaluation_id),
                str(candidate_id),
                manifest.model_dump_json(),
                manifest.manifest_sha256,
                report.model_dump_json(),
                report.report_sha256,
                timestamp,
                timestamp,
            ),
        )
    return database, candidate_id, candidate_release, evaluation_id, report


def test_review_is_idempotent_and_activation_requires_explicit_approval(
    settings, expert_package_payload
) -> None:
    database, candidate_id, candidate_release, evaluation_id, report = _prepared_candidate(
        settings, expert_package_payload
    )
    review_request = ExpertReviewCreate(
        client_request_id=uuid4(),
        candidate_sha256=KnowledgeRepository(database)
        .load_release(candidate_release.id)[0]
        .package_sha256,
        evaluation_sha256=report.report_sha256,
        decision="approve",
        reviewer_label="expert-1",
        reason="Les contrôles et le rapport sont suffisamment documentés.",
    )
    review, created = create_review(database, candidate_id, review_request)
    replay, replayed = create_review(database, candidate_id, review_request)
    assert created is True
    assert replayed is False
    assert replay == review

    activation_request = ExpertActivationRequest(
        client_request_id=uuid4(),
        review_id=review["id"],
        candidate_sha256=review_request.candidate_sha256,
        evaluation_sha256=report.report_sha256,
        expected_active_generation=0,
    )
    activation = activate_candidate(database, candidate_id, activation_request)
    assert activation["generation"] == 1
    assert activation["to_release_id"] == candidate_release.id
    replay_activation = activate_candidate(database, candidate_id, activation_request)
    assert replay_activation == activation
    with pytest.raises(ExpertReviewConflictError, match="activation client request id"):
        activate_candidate(
            database,
            candidate_id,
            activation_request.model_copy(update={"expected_active_generation": 1}),
        )
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT state FROM expert_candidates WHERE id = ?", (str(candidate_id),)
            ).fetchone()[0]
            == "activated"
        )
        assert connection.execute(
            "SELECT release_id, generation FROM expert_active_release WHERE singleton = 1"
        ).fetchone()[0] == str(candidate_release.id)
    target_release_id = activation["from_release_id"]
    target_release = KnowledgeRepository(database).load_release(target_release_id)[0]
    rollback_request = ExpertRollbackRequest(
        client_request_id=uuid4(),
        target_release_id=target_release_id,
        target_release_sha256=target_release.package_sha256,
        expected_active_generation=1,
        reason="Le pilote doit revenir à la release précédente.",
    )
    rollback = rollback_active_candidate(database, rollback_request)
    assert rollback["generation"] == 2
    assert rollback["to_release_id"] == target_release_id
    assert rollback_active_candidate(database, rollback_request) == rollback
    with pytest.raises(ExpertReviewConflictError, match="rollback client request id"):
        rollback_active_candidate(
            database,
            rollback_request.model_copy(update={"reason": "Une autre justification de rollback."}),
        )
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT state FROM expert_candidates WHERE id = ?", (str(candidate_id),)
            ).fetchone()[0]
            == "superseded"
        )


def test_review_rejects_inconclusive_evaluation(settings, expert_package_payload) -> None:
    database, candidate_id, candidate_release, _evaluation_id, report = _prepared_candidate(
        settings, expert_package_payload
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE expert_evaluations SET state = 'inconclusive' WHERE candidate_id = ?",
            (str(candidate_id),),
        )
    request = ExpertReviewCreate(
        client_request_id=uuid4(),
        candidate_sha256=KnowledgeRepository(database)
        .load_release(candidate_release.id)[0]
        .package_sha256,
        evaluation_sha256=report.report_sha256,
        decision="approve",
        reviewer_label="expert-1",
        reason="Ne pas promouvoir un résultat inconclusif.",
    )
    try:
        create_review(database, candidate_id, request)
    except ExpertReviewError as error:
        assert str(error) == "evaluation_not_passed"
    else:
        raise AssertionError("inconclusive evaluations must not be approvable")
