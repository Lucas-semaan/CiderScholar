"""Thin first-launch assistant routes backed by verified desktop services."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies import get_settings
from app.api.schemas import (
    ConfirmedDesktopAction,
    MemoryProfileRequest,
    SynchronizedRootRequest,
)
from app.config import Settings
from app.corpus_packages.distribution import DistributionPathError
from app.corpus_packages.installer import CorpusInstallError
from app.desktop.folder_picker import choose_synchronized_directory
from app.desktop.model_integrity import ModelIntegrityError
from app.desktop.onboarding import (
    OnboardingError,
    configure_synchronized_root,
    install_first_common_corpus,
    onboarding_status,
    select_memory_profile,
)
from app.desktop.system_checks import DesktopCompatibilityError
from app.desktop.user_config import UserConfigurationError

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

ONBOARDING_ERRORS = (
    OnboardingError,
    UserConfigurationError,
    DistributionPathError,
    CorpusInstallError,
    ModelIntegrityError,
    DesktopCompatibilityError,
)


def _conflict(error: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@router.get("")
def status(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, object]:
    return onboarding_status(settings).model_dump(mode="json")


@router.post("/choose-sharepoint")
def choose_sharepoint(payload: ConfirmedDesktopAction) -> dict[str, str | None]:
    selected = choose_synchronized_directory()
    return {"path": str(selected) if selected is not None else None}


@router.put("/sharepoint")
def configure_sharepoint(
    payload: SynchronizedRootRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    try:
        updated = configure_synchronized_root(
            settings,
            payload.path,
            confirm_unexpected_name=payload.confirm_unexpected_name,
        )
    except ONBOARDING_ERRORS as error:
        raise _conflict(error) from error
    request.app.state.settings = updated
    return onboarding_status(updated).model_dump(mode="json")


@router.post("/corpus")
def install_corpus(
    payload: ConfirmedDesktopAction,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    try:
        updated = install_first_common_corpus(settings)
    except ONBOARDING_ERRORS as error:
        raise _conflict(error) from error
    request.app.state.settings = updated
    return onboarding_status(updated).model_dump(mode="json")


@router.put("/memory")
def configure_memory(
    payload: MemoryProfileRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    try:
        updated = select_memory_profile(settings, payload.profile)
    except ONBOARDING_ERRORS as error:
        raise _conflict(error) from error
    request.app.state.settings = updated
    return onboarding_status(updated).model_dump(mode="json")
