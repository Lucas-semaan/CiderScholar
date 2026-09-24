from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.expert_feedback.models import CandidatePatch, ExpertCorrectionCreate, PatchOperation
from app.expert_feedback.repository import ExpertCorrectionRepository
from app.knowledge.models import KnowledgePackage
from app.knowledge.repository import KnowledgeRepository
from scripts import (
    compile_expert_candidate,
    diagnose_expert_feedback,
    prepare_expert_memory_evaluation,
    promote_expert_memory,
    rollback_expert_memory,
)
from tests.test_expert_candidate_compiler import _diagnosis
from tests.test_expert_memory_package import _approve_candidate


def _write_config_placeholder(path: Path) -> Path:
    path.write_text("placeholder", encoding="utf-8")
    return path


def test_diagnose_cli_persists_bounded_resume_artifacts(settings, monkeypatch, tmp_path) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation = database.create_chat_conversation("Diagnostic CLI")
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Réponse test"
    )
    message_id = UUID(database.chat_conversation(conversation["id"])["messages"][0]["id"])
    correction, _ = ExpertCorrectionRepository(database).submit(
        message_id,
        ExpertCorrectionCreate(
            client_request_id=uuid4(),
            problem="La réponse est insuffisamment bornée.",
            proposed_correction="Ajouter une limite vérifiable.",
            scope="reusable_method",
        ),
    )
    monkeypatch.setattr(diagnose_expert_feedback, "load_settings", lambda _path: settings)
    run_dir = Path("cli-diagnose")
    config = _write_config_placeholder(tmp_path / "config.yaml")

    assert (
        diagnose_expert_feedback.main(
            [
                "--correction-id",
                str(correction["id"]),
                "--expected-revision",
                "1",
                "--config",
                str(config),
                "--run-dir",
                str(run_dir),
            ]
        )
        == 0
    )
    assert (settings.paths.exports_dir / run_dir / "manifest.json").is_file()
    checkpoint = json.loads(
        (settings.paths.exports_dir / run_dir / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["status"] == "completed"
    assert checkpoint["created"] is True

    assert (
        diagnose_expert_feedback.main(
            [
                "--correction-id",
                str(correction["id"]),
                "--expected-revision",
                "1",
                "--config",
                str(config),
                "--run-dir",
                str(run_dir),
            ]
        )
        == 0
    )
    replay = json.loads(
        (settings.paths.exports_dir / run_dir / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert replay["created"] is False


def test_compile_cli_creates_isolated_candidate_and_rejects_external_run_dir(
    settings, expert_package_payload, monkeypatch, tmp_path
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    base_package = KnowledgePackage.model_validate(expert_package_payload)
    base = KnowledgeRepository(database).import_candidate(base_package)
    item = base_package.items[0]
    diagnosis_id, _correction_id, _message_id, diagnosis_hash = _diagnosis(
        database, item.id, item.content_sha256
    )
    patch = CandidatePatch(
        base_release_id=base.id,
        diagnosis_sha256=diagnosis_hash,
        operations=(
            PatchOperation(
                operation="replace_item",
                item_id=item.id,
                expected_sha256=item.content_sha256,
                item=item.model_copy(update={"body": "Version CLI bornée."}),
                justification="Corriger la borne observée dans le test CLI.",
            ),
        ),
    )
    patch_path = tmp_path / "patch.json"
    patch_path.write_text(patch.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(compile_expert_candidate, "load_settings", lambda _path: settings)
    config = _write_config_placeholder(tmp_path / "config.yaml")

    assert (
        compile_expert_candidate.main(
            [
                "--diagnosis-id",
                str(diagnosis_id),
                "--patch",
                str(patch_path),
                "--config",
                str(config),
                "--run-dir",
                "cli-compile",
            ]
        )
        == 0
    )
    report = json.loads(
        (settings.paths.exports_dir / "cli-compile" / "report.json").read_text(encoding="utf-8")
    )
    assert report["state"] == "structurally_valid"

    assert (
        compile_expert_candidate.main(
            [
                "--diagnosis-id",
                str(diagnosis_id),
                "--patch",
                str(patch_path),
                "--config",
                str(config),
                "--run-dir",
                "../outside",
            ]
        )
        == 1
    )


def test_prepare_evaluation_cli_writes_two_pinned_campaigns(
    settings, monkeypatch, tmp_path
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    base_release_id = uuid4()
    candidate_release_id = uuid4()
    messages = ["Question principale", "Contrôle indépendant", "Cas frontière"]
    hashes = [sha256(message.encode("utf-8")).hexdigest() for message in messages]
    manifest = {
        "candidate_id": str(uuid4()),
        "base_release_id": str(base_release_id),
        "candidate_release_id": str(candidate_release_id),
        "corpus_snapshot_sha256": "a" * 64,
        "configuration_sha256": "b" * 64,
        "code_revision": "test-revision",
        "split": "development",
        "question_hashes": hashes[:1],
        "control_question_hashes": hashes[1:],
        "mode": "abstract_only",
        "max_llm_requests": 0,
        "argo_authorized": False,
    }
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
    manifest_path = tmp_path / "manifest.json"
    cases_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cases_path.write_text(
        json.dumps(
            [
                {"question_id": "Q1", "profile": "p0", "message": messages[0]},
                {"question_id": "C1", "profile": "p1", "message": messages[1]},
                {"question_id": "C2", "profile": "p2", "message": messages[2]},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(prepare_expert_memory_evaluation, "load_settings", lambda _path: settings)
    config = _write_config_placeholder(tmp_path / "config.yaml")

    assert (
        prepare_expert_memory_evaluation.main(
            [
                "--manifest",
                str(manifest_path),
                "--cases",
                str(cases_path),
                "--run-id-prefix",
                "cli-evaluation",
                "--config",
                str(config),
                "--run-dir",
                "cli-evaluation",
            ]
        )
        == 0
    )
    artifact = json.loads(
        (settings.paths.exports_dir / "cli-evaluation" / "campaign_pair.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact["base"]["run_id"] == "cli-evaluation-base"
    assert artifact["candidate"]["run_id"] == "cli-evaluation-candidate"
    assert artifact["base"]["expert_memory_pin"]["release_id"] == str(base_release_id)

    assert (
        prepare_expert_memory_evaluation.main(
            [
                "--manifest",
                str(manifest_path),
                "--cases",
                str(cases_path),
                "--run-id-prefix",
                "cli-evaluation",
                "--config",
                str(config),
                "--run-dir",
                "cli-evaluation",
                "--advance",
            ]
        )
        == 0
    )
    checkpoint = json.loads(
        (settings.paths.exports_dir / "cli-evaluation" / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["status"] == "advanced"
    assert checkpoint["job_state"] == "queued"

    assert (
        prepare_expert_memory_evaluation.main(
            [
                "--manifest",
                str(manifest_path),
                "--cases",
                str(cases_path),
                "--run-id-prefix",
                "cli-evaluation",
                "--config",
                str(config),
                "--run-dir",
                "cli-evaluation",
                "--advance-pair",
            ]
        )
        == 0
    )
    pair_checkpoint = json.loads(
        (settings.paths.exports_dir / "cli-evaluation" / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert pair_checkpoint["status"] == "advanced_pair"
    assert pair_checkpoint["phase"] == "base"


def test_promotion_and_rollback_cli_share_preflight_gates(
    settings, expert_package_payload, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("CIDERSCHOLAR_LOCAL_PROFILE", "admin")
    database, candidate_id, candidate_release, _evaluation_id, _report, review = _approve_candidate(
        settings, expert_package_payload
    )
    with database.connect() as connection:
        base_release_id = UUID(
            str(
                connection.execute(
                    "SELECT base_release_id FROM expert_candidates WHERE id = ?",
                    (str(candidate_id),),
                ).fetchone()[0]
            )
        )
    monkeypatch.setattr(promote_expert_memory, "load_settings", lambda _path: settings)
    monkeypatch.setattr(rollback_expert_memory, "load_settings", lambda _path: settings)
    config = _write_config_placeholder(tmp_path / "config.yaml")

    assert (
        promote_expert_memory.main(
            [
                "--candidate-id",
                str(candidate_id),
                "--review-id",
                str(review["id"]),
                "--expected-active-generation",
                "0",
                "--config",
                str(config),
                "--output",
                "promotion.json",
            ]
        )
        == 0
    )
    promotion = json.loads(
        (settings.paths.exports_dir / "promotion.json").read_text(encoding="utf-8")
    )
    assert promotion["applied"] is False
    assert promotion["would_activate_release_id"] == str(candidate_release.id)

    assert (
        promote_expert_memory.main(
            [
                "--candidate-id",
                str(candidate_id),
                "--review-id",
                str(review["id"]),
                "--expected-active-generation",
                "0",
                "--config",
                str(config),
                "--apply",
            ]
        )
        == 0
    )
    assert (
        rollback_expert_memory.main(
            [
                "--release-id",
                str(base_release_id),
                "--expected-active-generation",
                "1",
                "--reason",
                "Retour explicite après vérification locale.",
                "--config",
                str(config),
                "--apply",
            ]
        )
        == 0
    )
    assert KnowledgeRepository(database).resolve_active_release().release_id == base_release_id
