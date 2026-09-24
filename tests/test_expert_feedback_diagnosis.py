from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.expert_feedback.diagnosis import list_diagnoses, persist_diagnosis
from app.expert_feedback.models import ExpertCorrectionCreate
from app.expert_feedback.repository import ExpertCorrectionRepository
from app.jobs.contracts import ChatAnswerPayload
from app.jobs.repository import JobRepository
from app.knowledge.trace import (
    ExpertRunManifest,
    TraceCandidate,
    TraceCost,
    TraceIdentity,
    TraceOutput,
)


def test_missing_manifest_is_persisted_as_uncertain_diagnosis(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    conversation = database.create_chat_conversation("Diagnostic sans trace")
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Réponse sans trace."
    )
    stored = database.chat_conversation(conversation["id"])
    assert stored is not None
    message_id = stored["messages"][0]["id"]
    correction, _ = ExpertCorrectionRepository(database).submit(
        UUID(message_id),
        ExpertCorrectionCreate(
            client_request_id=uuid4(),
            problem="La réponse doit expliciter sa méthode.",
            proposed_correction="Ajouter la méthode observée dans le corpus.",
            scope="this_answer",
        ),
    )

    diagnosis, created = persist_diagnosis(
        database, correction_id=correction["id"], expected_revision=1
    )

    assert created is True
    assert diagnosis["payload"]["primary_cause"] == "insufficient_trace"
    assert diagnosis["payload"]["confidence"] == "uncertain"
    with database.connect() as connection:
        assert (
            connection.execute("SELECT status FROM expert_corrections").fetchone()[0]
            == "needs_expert"
        )
        assert connection.execute("SELECT COUNT(*) FROM expert_diagnoses").fetchone()[0] == 1


def test_semantic_filter_signal_stays_uncertain_and_replays(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    conversation = database.create_chat_conversation("Diagnostic avec trace")
    payload = ChatAnswerPayload(
        message="Question scientifique",
        conversation_id=UUID(conversation["id"]),
        client_request_id=uuid4(),
    )
    queued = repository.enqueue_chat(payload)
    database.append_chat_message(
        conversation_id=conversation["id"], role="assistant", content="Réponse tracée."
    )
    stored = database.chat_conversation(conversation["id"])
    assert stored is not None
    assistant = UUID(stored["messages"][1]["id"])
    digest = "a" * 64
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    manifest = ExpertRunManifest(
        run_id=uuid4(),
        job_id=queued.job.id,
        attempt=queued.job.attempt,
        code_revision="test",
        question_sha256=digest,
        user_context_sha256=digest,
        answer_effort="balanced",
        interaction_mode="research",
        configuration_sha256=digest,
        sql_schema_version=53,
        candidates=(
            TraceCandidate(
                identity=TraceIdentity(item_id="common.article", revision=1, content_sha256=digest),
                stage="semantic_filter",
                rank=0,
                decision="rejected",
                reason="global_semantic_grade_c_or_d",
            ),
        ),
        output=TraceOutput(
            state="succeeded",
            response_sha256=sha256("Réponse tracée.".encode()).hexdigest(),
        ),
        cost=TraceCost(),
        state="succeeded",
        created_at=now,
        updated_at=now,
    )
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO expert_run_manifests(
                id, job_id, attempt, payload_json, manifest_sha256, state,
                result_message_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)
            """,
            (
                str(manifest.run_id),
                str(manifest.job_id),
                manifest.attempt,
                manifest.model_dump_json(),
                manifest.manifest_sha256(),
                str(assistant),
                now.isoformat(),
                now.isoformat(),
            ),
        )
    correction, _ = ExpertCorrectionRepository(database).submit(
        assistant,
        ExpertCorrectionCreate(
            client_request_id=uuid4(),
            problem="La réponse écarte une preuve pertinente.",
            proposed_correction="Réexaminer le filtre sémantique appliqué.",
            scope="this_answer",
        ),
    )

    first, created = persist_diagnosis(
        database, correction_id=correction["id"], expected_revision=1
    )
    replay, replayed = persist_diagnosis(
        database, correction_id=correction["id"], expected_revision=1
    )

    assert created is True
    assert replayed is False
    assert first == replay
    assert first["payload"]["primary_cause"] == "semantic_filter_error"
    assert first["payload"]["confidence"] == "uncertain"
    assert list_diagnoses(database, correction["id"]) == [first]
