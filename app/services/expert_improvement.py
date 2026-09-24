"""Application service for bounded, local expert-feedback diagnosis."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.database.sqlite import Database
from app.expert_feedback.candidates import ExpertCandidateCompiler
from app.expert_feedback.diagnosis import persist_diagnosis
from app.expert_feedback.models import CandidatePatch


class ExpertImprovementService:
    """Orchestrate deterministic diagnosis without prompts or provider calls."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def diagnose_correction(
        self,
        correction_id: UUID,
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        return persist_diagnosis(
            self.database,
            correction_id=correction_id,
            expected_revision=expected_revision,
            now=now,
        )

    def compile_candidate(
        self,
        patch: CandidatePatch,
        *,
        diagnosis_id: UUID,
        now: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Create an isolated candidate release; never alter the active release."""

        return ExpertCandidateCompiler(self.database).compile(
            patch, diagnosis_id=diagnosis_id, now=now
        )
