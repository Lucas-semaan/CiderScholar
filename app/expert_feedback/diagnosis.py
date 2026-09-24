"""Deterministic, content-free diagnosis of private expert corrections."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.expert_feedback.models import Diagnosis
from app.expert_feedback.repository import (
    ExpertCorrectionConflictError,
    ExpertCorrectionValidationError,
)
from app.knowledge.trace import ExpertRunManifest


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _diagnosis_hash(diagnosis: Diagnosis) -> str:
    return hashlib.sha256(diagnosis.model_dump_json().encode("utf-8")).hexdigest()


def build_diagnosis(
    connection: sqlite3.Connection,
    *,
    correction_id: UUID,
    correction_revision: int,
) -> Diagnosis:
    """Build one bounded diagnosis from persisted correction and trace data only."""

    correction = connection.execute(
        """
        SELECT id, message_id, manifest_id, revision, payload_json
        FROM expert_corrections WHERE id = ?
        """,
        (str(correction_id),),
    ).fetchone()
    if correction is None:
        raise ExpertCorrectionValidationError("correction does not exist")
    if int(correction["revision"]) != correction_revision:
        raise ExpertCorrectionConflictError("correction revision has changed")

    manifest_id = correction["manifest_id"]
    if manifest_id is None:
        return _incomplete_diagnosis(
            correction_id,
            correction_revision,
            observed_ids=(str(correction_id),),
            missing_information=("manifest", "persisted_run_inputs"),
            rationale=(
                "Aucun manifeste n'est attaché à cette réponse ; les entrées nécessaires ne "
                "peuvent pas être reconstituées sans simuler une cause."
            ),
        )

    message = connection.execute(
        "SELECT role, content FROM chat_messages WHERE id = ?",
        (str(correction["message_id"]),),
    ).fetchone()
    manifest_row = connection.execute(
        """
        SELECT payload_json FROM expert_run_manifests
        WHERE id = ? AND result_message_id = ?
        """,
        (str(manifest_id), str(correction["message_id"])),
    ).fetchone()
    if message is None or message["role"] != "assistant" or manifest_row is None:
        return _incomplete_diagnosis(
            correction_id,
            correction_revision,
            observed_ids=(str(correction_id), str(manifest_id)),
            missing_information=("attached_manifest", "assistant_message"),
            rationale=(
                "La relation entre la correction, la réponse assistant et le manifeste n'est "
                "pas vérifiable."
            ),
        )
    try:
        manifest = ExpertRunManifest.model_validate_json(manifest_row["payload_json"])
    except ValueError:
        return _incomplete_diagnosis(
            correction_id,
            correction_revision,
            observed_ids=(str(manifest_id),),
            missing_information=("valid_manifest_payload",),
            rationale="Le manifeste persisté ne peut pas être relu avec le contrat courant.",
        )

    observed_ids = [str(manifest_id)]
    verified_hashes = [manifest.manifest_sha256()]
    if manifest.output.response_sha256 is not None:
        response_hash = hashlib.sha256(message["content"].encode("utf-8")).hexdigest()
        if response_hash != manifest.output.response_sha256:
            return Diagnosis(
                correction_id=correction_id,
                correction_revision=correction_revision,
                primary_cause="source_changed",
                observed_ids=tuple(observed_ids),
                verified_hashes=tuple(verified_hashes),
                rationale=(
                    "Le hash de la réponse persistée diffère de celui du manifeste ; ce run n'est "
                    "pas comparable à la réponse actuellement ciblée."
                ),
                missing_information=("matching_response_snapshot",),
                confidence="supported",
                proposed_action="expert_review",
            )

    if manifest.state in {"failed", "cancelled"} or manifest.output.state in {
        "failed",
        "cancelled",
    }:
        return Diagnosis(
            correction_id=correction_id,
            correction_revision=correction_revision,
            primary_cause="runtime_failure",
            observed_ids=tuple(observed_ids),
            verified_hashes=tuple(verified_hashes),
            rationale=(
                "Le manifeste indique une exécution interrompue ; aucune cause scientifique "
                "n'est attribuée."
            ),
            confidence="supported",
            proposed_action="engineering_issue",
        )
    if manifest.state != "succeeded" or manifest.output.state != "succeeded":
        return _incomplete_diagnosis(
            correction_id,
            correction_revision,
            observed_ids=tuple(observed_ids),
            missing_information=("completed_manifest",),
            rationale=(
                "Le manifeste n'est pas clôturé comme une réponse réussie ; le diagnostic "
                "causal reste incertain."
            ),
            verified_hashes=tuple(verified_hashes),
        )

    if manifest.output.validation_codes:
        observed_ids.extend(f"validation:{code}" for code in manifest.output.validation_codes)
        return Diagnosis(
            correction_id=correction_id,
            correction_revision=correction_revision,
            primary_cause="validator_bug",
            observed_ids=tuple(observed_ids),
            verified_hashes=tuple(verified_hashes),
            rationale=(
                "La trace conserve un rejet de validation ; il doit être reproduit par un test "
                "Python avant toute correction de méthode."
            ),
            confidence="uncertain",
            proposed_action="engineering_issue",
        )

    semantic_rejections = [
        candidate
        for candidate in manifest.candidates
        if candidate.stage == "semantic_filter" and candidate.decision == "rejected"
    ]
    if semantic_rejections:
        observed_ids.extend(candidate.identity.item_id for candidate in semantic_rejections[:20])
        return Diagnosis(
            correction_id=correction_id,
            correction_revision=correction_revision,
            primary_cause="semantic_filter_error",
            observed_ids=tuple(observed_ids),
            verified_hashes=tuple(verified_hashes),
            rationale=(
                "Des candidats ont été rejetés par le filtre sémantique ; la trace signale un "
                "point à examiner mais ne prouve pas encore que le filtre est fautif."
            ),
            confidence="uncertain",
            proposed_action="expert_review",
        )

    omitted_candidates = [
        candidate
        for candidate in manifest.candidates
        if candidate.decision == "omitted" and candidate.stage in {"retrieval", "fusion"}
    ]
    if omitted_candidates:
        observed_ids.extend(candidate.identity.item_id for candidate in omitted_candidates[:20])
        return Diagnosis(
            correction_id=correction_id,
            correction_revision=correction_revision,
            primary_cause="retrieval_error",
            observed_ids=tuple(observed_ids),
            verified_hashes=tuple(verified_hashes),
            rationale=(
                "Des candidats ont été omis avant le contexte final ; leur pertinence et la "
                "limite responsable doivent être vérifiées séparément."
            ),
            confidence="uncertain",
            proposed_action="expert_review",
        )

    return Diagnosis(
        correction_id=correction_id,
        correction_revision=correction_revision,
        primary_cause="expert_ambiguity",
        observed_ids=tuple(observed_ids),
        verified_hashes=tuple(verified_hashes),
        rationale=(
            "La trace réussie ne démontre pas une cause unique parmi les couches observées ; "
            "une décision experte reste nécessaire."
        ),
        confidence="uncertain",
        proposed_action="expert_review",
    )


def persist_diagnosis(
    database: Database,
    *,
    correction_id: UUID,
    expected_revision: int,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Persist one immutable diagnosis, replaying an existing revision deterministically."""

    created_at = now or datetime.now(UTC)
    with database.transaction() as connection:
        diagnosis = build_diagnosis(
            connection,
            correction_id=correction_id,
            correction_revision=expected_revision,
        )
        diagnosis_sha256 = _diagnosis_hash(diagnosis)
        existing = connection.execute(
            """
            SELECT id, correction_id, correction_revision, payload_json,
                   diagnosis_sha256, created_at
            FROM expert_diagnoses
            WHERE correction_id = ? AND correction_revision = ? AND diagnosis_sha256 = ?
            """,
            (str(correction_id), expected_revision, diagnosis_sha256),
        ).fetchone()
        if existing is not None:
            return _public_diagnosis(existing), False
        timestamp = _timestamp(created_at)
        diagnosis_id = uuid4()
        connection.execute(
            """
            INSERT INTO expert_diagnoses(
                id, correction_id, correction_revision, payload_json, diagnosis_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(diagnosis_id),
                str(correction_id),
                expected_revision,
                diagnosis.model_dump_json(),
                diagnosis_sha256,
                timestamp,
            ),
        )
        connection.execute(
            """
            UPDATE expert_corrections
            SET status = ?, updated_at = ?
            WHERE id = ? AND revision = ?
            """,
            (
                "diagnosed" if diagnosis.confidence == "supported" else "needs_expert",
                timestamp,
                str(correction_id),
                expected_revision,
            ),
        )
        return {
            "id": diagnosis_id,
            "correction_id": correction_id,
            "correction_revision": expected_revision,
            "diagnosis_sha256": diagnosis_sha256,
            "payload": diagnosis.model_dump(mode="json"),
            "created_at": created_at,
        }, True


def list_diagnoses(database: Database, correction_id: UUID) -> list[dict[str, object]]:
    """Return immutable diagnoses for one correction, newest revision first."""

    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, correction_id, correction_revision, payload_json,
                   diagnosis_sha256, created_at
            FROM expert_diagnoses
            WHERE correction_id = ?
            ORDER BY correction_revision DESC, created_at DESC, id DESC
            """,
            (str(correction_id),),
        ).fetchall()
    return [_public_diagnosis(row) for row in rows]


def _incomplete_diagnosis(
    correction_id: UUID,
    correction_revision: int,
    *,
    observed_ids: tuple[str, ...],
    missing_information: tuple[str, ...],
    rationale: str,
    verified_hashes: tuple[str, ...] = (),
) -> Diagnosis:
    return Diagnosis(
        correction_id=correction_id,
        correction_revision=correction_revision,
        primary_cause="insufficient_trace",
        observed_ids=observed_ids,
        verified_hashes=verified_hashes,
        rationale=rationale,
        missing_information=missing_information,
        confidence="uncertain",
        proposed_action="expert_review",
    )


def _public_diagnosis(row: sqlite3.Row) -> dict[str, object]:
    diagnosis = Diagnosis.model_validate_json(row["payload_json"])
    return {
        "id": UUID(str(row["id"])),
        "correction_id": UUID(str(row["correction_id"])),
        "correction_revision": int(row["correction_revision"]),
        "diagnosis_sha256": row["diagnosis_sha256"],
        "payload": diagnosis.model_dump(mode="json"),
        "created_at": datetime.fromisoformat(row["created_at"]),
    }
