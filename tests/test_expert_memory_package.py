from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.database.sqlite import Database
from app.expert_feedback.models import ExpertReviewCreate
from app.expert_feedback.review import create_review
from app.knowledge.package import (
    ExpertMemoryPackageError,
    activate_expert_memory_distribution,
    approve_expert_memory_distribution,
    build_approved_expert_memory_package,
    get_expert_memory_distribution,
    import_staged_expert_memory_package,
    propose_expert_memory_distribution,
    rollback_expert_memory_distribution,
    sign_expert_memory_package,
    stage_expert_memory_package,
    verify_expert_memory_package,
)
from app.knowledge.repository import KnowledgeRepository


def _approve_candidate(settings, expert_package_payload):
    from tests.test_expert_review import _prepared_candidate

    database, candidate_id, candidate_release, evaluation_id, report = _prepared_candidate(
        settings, expert_package_payload
    )
    candidate_sha256 = (
        KnowledgeRepository(database).load_release(candidate_release.id)[0].package_sha256
    )
    review, _ = create_review(
        database,
        candidate_id,
        ExpertReviewCreate(
            client_request_id=uuid4(),
            candidate_sha256=candidate_sha256,
            evaluation_sha256=report.report_sha256,
            decision="approve",
            reviewer_label="expert-1",
            reason="Le rapport et les contrôles sont vérifiés.",
        ),
    )
    return database, candidate_id, candidate_release, evaluation_id, report, review


def test_only_approved_memory_is_exported_without_private_feedback(
    settings, expert_package_payload, tmp_path
) -> None:
    database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        database, candidate_id, output_root=tmp_path / "packages"
    )
    verified = verify_expert_memory_package(built.package_directory)
    assert verified == built.package
    assert len(verified.approved_review_sha256) == 64
    raw = (tmp_path / "packages" / verified.package_sha256 / "expert-memory.json").read_text()
    assert "Le rapport et les contrôles" not in raw
    assert "conversation" not in raw.lower()
    assert "job" not in raw.lower()


def test_unapproved_candidate_cannot_be_exported(
    settings, expert_package_payload, tmp_path
) -> None:
    from tests.test_expert_review import _prepared_candidate

    database, candidate_id, _release, _evaluation_id, _report = _prepared_candidate(
        settings, expert_package_payload
    )
    with pytest.raises(ExpertMemoryPackageError, match="approved_review_not_found"):
        build_approved_expert_memory_package(
            database, candidate_id, output_root=tmp_path / "packages"
        )


def test_package_tampering_is_rejected(settings, expert_package_payload, tmp_path) -> None:
    database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        database, candidate_id, output_root=tmp_path / "packages"
    )
    path = tmp_path / "packages" / built.package.package_sha256 / "expert-memory.json"
    payload = json.loads(path.read_text())
    payload["knowledge"]["items"][0]["title"] = "Tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ExpertMemoryPackageError, match="manifest_hash_mismatch"):
        verify_expert_memory_package(path.parent)


def test_signature_is_required_when_requested(settings, expert_package_payload, tmp_path) -> None:
    database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        database, candidate_id, output_root=tmp_path / "packages"
    )
    with pytest.raises(ExpertMemoryPackageError, match="allowed_signers_required"):
        verify_expert_memory_package(built.package_directory, require_signature=True)


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="Windows OpenSSH is unavailable")
def test_ed25519_signature_rejects_tampered_memory_package(
    settings, expert_package_payload, tmp_path
) -> None:
    database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        database, candidate_id, output_root=tmp_path / "packages"
    )
    key = tmp_path / "signing"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"ciderscholar-admin {public_key}\n", encoding="utf-8")
    signed = sign_expert_memory_package(
        built.package_directory,
        private_key=key,
        signer_identity="ciderscholar-admin",
    )
    assert signed.artifact == "expert-memory.json"
    assert (
        verify_expert_memory_package(
            built.package_directory, allowed_signers=allowed, require_signature=True
        )
        == built.package
    )
    (Path(built.package_path)).write_text(
        Path(built.package_path).read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    with pytest.raises(ExpertMemoryPackageError, match="signature_artifact_mismatch"):
        verify_expert_memory_package(
            built.package_directory, allowed_signers=allowed, require_signature=True
        )


def test_staging_and_import_are_idempotent_and_never_activate(
    settings, expert_package_payload, tmp_path
) -> None:
    source_database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        source_database, candidate_id, output_root=tmp_path / "packages"
    )
    staged = stage_expert_memory_package(built.package_directory, staging_root=tmp_path / "staging")
    target_database = Database(tmp_path / "target.sqlite3")
    target_database.initialize()
    imported = import_staged_expert_memory_package(target_database, staged)
    replayed = import_staged_expert_memory_package(target_database, staged)
    assert imported == replayed
    assert imported.package_sha256 == staged.package.package_sha256
    assert KnowledgeRepository(target_database).resolve_active_release().release_id is None


def test_distribution_requires_local_approval_before_atomic_activation(
    settings, expert_package_payload, tmp_path
) -> None:
    source_database, candidate_id, _release, _evaluation_id, _report, _review = _approve_candidate(
        settings, expert_package_payload
    )
    built = build_approved_expert_memory_package(
        source_database, candidate_id, output_root=tmp_path / "packages"
    )
    staged = stage_expert_memory_package(built.package_directory, staging_root=tmp_path / "staging")
    target_database = Database(tmp_path / "target.sqlite3")
    target_database.initialize()
    imported = import_staged_expert_memory_package(target_database, staged)
    with target_database.connect() as connection:
        distribution_id = UUID(
            str(
                connection.execute(
                    "SELECT id FROM expert_memory_distributions WHERE release_id = ?",
                    (str(imported.id),),
                ).fetchone()[0]
            )
        )
    proposed = propose_expert_memory_distribution(target_database, distribution_id)
    assert proposed["state"] == "proposed"
    with pytest.raises(ExpertMemoryPackageError, match="distribution_not_approved"):
        activate_expert_memory_distribution(
            target_database,
            distribution_id,
            client_request_id=uuid4(),
            expected_active_generation=0,
            expected_active_release_id=None,
        )
    approved = approve_expert_memory_distribution(
        target_database,
        distribution_id,
        reviewer_label="local-admin",
        reason="Paquet signé et compatible vérifié.",
    )
    assert approved["state"] == "approved"
    request_id = uuid4()
    event = activate_expert_memory_distribution(
        target_database,
        distribution_id,
        client_request_id=request_id,
        expected_active_generation=0,
        expected_active_release_id=None,
    )
    replay = activate_expert_memory_distribution(
        target_database,
        distribution_id,
        client_request_id=request_id,
        expected_active_generation=0,
        expected_active_release_id=None,
    )
    assert replay == event
    with pytest.raises(ExpertMemoryPackageError, match="activation_request_conflict"):
        activate_expert_memory_distribution(
            target_database,
            distribution_id,
            client_request_id=request_id,
            expected_active_generation=1,
            expected_active_release_id=imported.id,
        )
    assert event["generation"] == 1
    assert KnowledgeRepository(target_database).resolve_active_release().release_id == imported.id
    assert get_expert_memory_distribution(target_database, distribution_id)["state"] == "activated"


def test_distributed_activation_can_be_explicitly_rolled_back(
    settings, expert_package_payload, tmp_path
) -> None:
    source_database, candidate_id, _candidate_release, _evaluation_id, _report, _review = (
        _approve_candidate(settings, expert_package_payload)
    )
    built = build_approved_expert_memory_package(
        source_database, candidate_id, output_root=tmp_path / "packages"
    )
    staged = stage_expert_memory_package(built.package_directory, staging_root=tmp_path / "staging")
    target_database = Database(tmp_path / "target.sqlite3")
    target_database.initialize()
    with source_database.connect() as connection:
        base_release_id = UUID(
            str(
                connection.execute(
                    "SELECT base_release_id FROM expert_candidates WHERE id = ?",
                    (str(candidate_id),),
                ).fetchone()[0]
            )
        )
    _stored_base, base_package = KnowledgeRepository(source_database).load_release(base_release_id)
    imported_base = KnowledgeRepository(target_database).import_candidate(base_package)
    with target_database.transaction() as connection:
        connection.execute(
            "UPDATE expert_releases SET state = 'eligible' WHERE id = ?",
            (str(imported_base.id),),
        )
        connection.execute(
            "UPDATE expert_active_release SET release_id = ?, generation = 0 WHERE singleton = 1",
            (str(imported_base.id),),
        )
    imported = import_staged_expert_memory_package(target_database, staged)
    with target_database.connect() as connection:
        distribution_id = UUID(
            str(
                connection.execute(
                    "SELECT id FROM expert_memory_distributions WHERE release_id = ?",
                    (str(imported.id),),
                ).fetchone()[0]
            )
        )
    propose_expert_memory_distribution(target_database, distribution_id)
    approve_expert_memory_distribution(
        target_database,
        distribution_id,
        reviewer_label="local-admin",
        reason="Activation locale vérifiée.",
    )
    activation = activate_expert_memory_distribution(
        target_database,
        distribution_id,
        client_request_id=uuid4(),
        expected_active_generation=0,
        expected_active_release_id=imported_base.id,
    )
    assert activation["generation"] == 1
    rollback_request = uuid4()
    rollback = rollback_expert_memory_distribution(
        target_database,
        distribution_id,
        client_request_id=rollback_request,
        target_release_id=imported_base.id,
        target_release_sha256=base_package.package_sha256,
        expected_active_generation=1,
        expected_active_release_id=imported.id,
        reason="Retour arrière explicite après vérification locale.",
    )
    replay = rollback_expert_memory_distribution(
        target_database,
        distribution_id,
        client_request_id=rollback_request,
        target_release_id=imported_base.id,
        target_release_sha256=base_package.package_sha256,
        expected_active_generation=1,
        expected_active_release_id=imported.id,
        reason="Retour arrière explicite après vérification locale.",
    )
    assert replay == rollback
    with pytest.raises(ExpertMemoryPackageError, match="rollback_request_conflict"):
        rollback_expert_memory_distribution(
            target_database,
            distribution_id,
            client_request_id=rollback_request,
            target_release_id=imported_base.id,
            target_release_sha256=base_package.package_sha256,
            expected_active_generation=1,
            expected_active_release_id=imported.id,
            reason="Une justification différente ne doit pas rejouer la même clé.",
        )
    assert rollback["generation"] == 2
    assert (
        KnowledgeRepository(target_database).resolve_active_release().release_id == imported_base.id
    )
    assert (
        get_expert_memory_distribution(target_database, distribution_id)["state"] == "rolled_back"
    )
