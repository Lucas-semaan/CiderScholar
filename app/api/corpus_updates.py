"""Confirmed user actions for locally synchronized common-corpus updates."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_settings
from app.api.schemas import ConfirmedCorpusAction
from app.config import Settings
from app.corpus_packages.actions import (
    download_and_validate_available_update,
    mark_validated_update_ready,
)
from app.corpus_packages.activation import schedule_previous_rollback
from app.corpus_packages.installer import CorpusInstallError

router = APIRouter(prefix="/api/corpus-updates", tags=["corpus-updates"])


def _conflict(error: CorpusInstallError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@router.post("/download")
def download_update(
    _: ConfirmedCorpusAction,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    try:
        validated = download_and_validate_available_update(settings)
    except CorpusInstallError as exc:
        raise _conflict(exc) from exc
    return {
        "state": "validated",
        "corpus_version": validated.manifest.corpus_version,
        "message": "Mise à jour téléchargée et vérifiée. Le corpus actif est inchangé.",
    }


@router.post("/install-on-restart")
def install_update_on_restart(
    _: ConfirmedCorpusAction,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    try:
        ready = mark_validated_update_ready(settings)
    except CorpusInstallError as exc:
        raise _conflict(exc) from exc
    return {
        "state": "ready",
        "corpus_version": ready.corpus_version,
        "message": "Installation planifiée au prochain redémarrage.",
    }


@router.post("/rollback-on-restart")
def rollback_update_on_restart(
    _: ConfirmedCorpusAction,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    try:
        scheduled = schedule_previous_rollback(settings)
    except CorpusInstallError as exc:
        raise _conflict(exc) from exc
    return {
        "state": "rollback_ready",
        "previous_path": scheduled.previous_path,
        "message": "Retour à la version précédente planifié au prochain redémarrage.",
    }
