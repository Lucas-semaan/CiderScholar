"""Single-shot suggestion submission routes; intentionally no tracking or listing API."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.dependencies import get_settings
from app.api.schemas import SuggestionReferenceRequest
from app.config import Settings
from app.suggestions.models import SuggestionSubmissionResult
from app.suggestions.service import submit_pdf_suggestion, submit_reference_suggestion

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


@router.post("", response_model=SuggestionSubmissionResult)
def submit_reference(
    payload: SuggestionReferenceRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SuggestionSubmissionResult:
    return submit_reference_suggestion(
        settings,
        payload.source,
        scientific_comment=payload.scientific_comment,
    )


@router.post("/pdf", response_model=SuggestionSubmissionResult)
async def submit_pdf(
    settings: Annotated[Settings, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
    transmit_pdf_confirmed: Annotated[Literal[True], Form()],
    scientific_comment: Annotated[str | None, Form(max_length=1500)] = None,
) -> SuggestionSubmissionResult:
    payload = await file.read(settings.suggestions.maximum_pdf_bytes + 1)
    return submit_pdf_suggestion(
        settings,
        filename=file.filename or "",
        payload=payload,
        scientific_comment=scientific_comment,
        transmit_pdf_confirmed=transmit_pdf_confirmed,
    )
