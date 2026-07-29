"""Immediate evaluation and durable handoff workflow for user suggestions."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.corpus_packages.distribution import DistributionPathError
from app.llm.argo_client import ArgoError
from app.suggestions.context import context_from_reference, extract_pdf_candidate
from app.suggestions.evaluation import (
    SuggestionDecisionError,
    SuggestionEvaluationUnavailable,
    decision_is_accepted,
    evaluate_suggestion,
)
from app.suggestions.models import (
    DoiSuggestionSource,
    ManualSuggestionSource,
    PdfSuggestionSource,
    SuggestionArgoDecision,
    SuggestionCandidateContext,
    SuggestionDraft,
    SuggestionSubmissionResult,
    UrlSuggestionSource,
)
from app.suggestions.packaging import (
    DuplicateSuggestionError,
    SuggestionPackageError,
    build_suggestion_package,
    ensure_not_duplicate,
    transmit_prepared_package,
)
from app.suggestions.validation import validate_pdf_payload

ReferenceSource = DoiSuggestionSource | UrlSuggestionSource | ManualSuggestionSource
Evaluator = Callable[
    [Settings, SuggestionDraft, SuggestionCandidateContext], SuggestionArgoDecision
]


def _evaluate_and_submit(
    settings: Settings,
    draft: SuggestionDraft,
    candidate: SuggestionCandidateContext,
    *,
    pdf_path: Path | None = None,
    evaluator: Evaluator | None = None,
) -> SuggestionSubmissionResult:
    try:
        ensure_not_duplicate(settings, draft, candidate)
    except DuplicateSuggestionError as exc:
        return SuggestionSubmissionResult(
            suggestion_id=draft.suggestion_id,
            state="not_retained",
            message=str(exc),
        )
    try:
        decision = (evaluator or evaluate_suggestion)(settings, draft, candidate)
    except SuggestionEvaluationUnavailable as exc:
        return SuggestionSubmissionResult(
            suggestion_id=draft.suggestion_id,
            state="retry",
            message=str(exc),
            action="settings",
        )
    except (ArgoError, SuggestionDecisionError) as exc:
        return SuggestionSubmissionResult(
            suggestion_id=draft.suggestion_id,
            state="retry",
            message=f"Évaluation ARGO indisponible ({type(exc).__name__}). Réessayez plus tard.",
            action="retry",
        )
    if not decision_is_accepted(decision, settings.suggestions.acceptance_threshold):
        return SuggestionSubmissionResult(
            suggestion_id=draft.suggestion_id,
            state="not_retained",
            message="Suggestion évaluée mais non retenue selon le seuil conservateur.",
            decision=decision,
        )
    prepared = build_suggestion_package(
        settings,
        draft,
        candidate,
        decision,
        pdf_path=pdf_path,
    )
    try:
        transmit_prepared_package(settings, prepared)
    except (DistributionPathError, OSError, SuggestionPackageError):
        return SuggestionSubmissionResult(
            suggestion_id=draft.suggestion_id,
            state="retry",
            message=(
                "Suggestion acceptée par ARGO mais SharePoint est indisponible. "
                "Le paquet local sera renvoyé au prochain lancement."
            ),
            action="retry",
            decision=decision,
        )
    return SuggestionSubmissionResult(
        suggestion_id=draft.suggestion_id,
        state="accepted",
        message=(
            "Suggestion acceptée et transmise. Elle n'est pas encore dans le RAG commun : "
            "l'administrateur l'examinera lors de la maintenance hebdomadaire."
        ),
        decision=decision,
    )


def submit_reference_suggestion(
    settings: Settings,
    source: ReferenceSource,
    *,
    scientific_comment: str | None = None,
    evaluator: Evaluator | None = None,
) -> SuggestionSubmissionResult:
    """Evaluate DOI, URL or manual metadata without downloading any referenced URL."""

    draft = SuggestionDraft(
        created_at=datetime.now(UTC),
        source=source,
        scientific_comment=scientific_comment,
    )
    return _evaluate_and_submit(
        settings,
        draft,
        context_from_reference(source),
        evaluator=evaluator,
    )


def submit_pdf_suggestion(
    settings: Settings,
    *,
    filename: str,
    payload: bytes,
    scientific_comment: str | None,
    transmit_pdf_confirmed: bool,
    evaluator: Evaluator | None = None,
) -> SuggestionSubmissionResult:
    if not transmit_pdf_confirmed:
        raise ValueError("La transmission du PDF doit être confirmée explicitement.")
    internal_name, digest = validate_pdf_payload(
        filename,
        payload,
        maximum_bytes=settings.suggestions.maximum_pdf_bytes,
    )
    source = PdfSuggestionSource(
        internal_filename=internal_name,
        size_bytes=len(payload),
        sha256=digest,
    )
    draft = SuggestionDraft(
        created_at=datetime.now(UTC),
        source=source,
        scientific_comment=scientific_comment,
    )
    settings.paths.cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="suggestion-input-",
        dir=settings.paths.cache_dir,
    ) as temporary:
        pdf_path = Path(temporary) / internal_name
        pdf_path.write_bytes(payload)
        candidate = extract_pdf_candidate(
            source,
            pdf_path,
            maximum_context_characters=settings.suggestions.maximum_context_characters,
        )
        return _evaluate_and_submit(
            settings,
            draft,
            candidate,
            pdf_path=pdf_path,
            evaluator=evaluator,
        )
