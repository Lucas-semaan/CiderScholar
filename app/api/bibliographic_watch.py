"""Administrator-only configuration and controls for additive bibliographic watch."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.admin_maintenance import _admin_profile
from app.api.dependencies import get_settings
from app.config import Settings
from app.jobs.contracts import JobPublic
from app.jobs.repository import EvaluationRunBusyError
from app.resource_lock import ResourceBusyError
from app.updates.watch_models import WatchAction, WatchConfiguration, WatchReport, WatchStatus
from app.updates.watch_store import WatchStore

router = APIRouter(
    prefix="/api/admin/bibliographic-watch",
    tags=["bibliographic-watch"],
    dependencies=[Depends(_admin_profile)],
)


@router.get("", response_model=WatchStatus)
def status(settings: Annotated[Settings, Depends(get_settings)]) -> WatchStatus:
    return WatchStore(settings).status()


@router.get("/history", response_model=list[WatchReport])
def history(settings: Annotated[Settings, Depends(get_settings)]) -> list[WatchReport]:
    return WatchStore(settings).status().history


@router.put("/configuration", response_model=WatchStatus)
def configure(
    config: WatchConfiguration, settings: Annotated[Settings, Depends(get_settings)]
) -> WatchStatus:
    store = WatchStore(settings)
    try:
        store.configure(config)
    except ResourceBusyError as error:
        raise HTTPException(status_code=409, detail="Réglages occupés ; réessayez.") from error
    try:
        store.enqueue()
    except (ResourceBusyError, EvaluationRunBusyError):
        return store.status()
    return store.status()


@router.post("/launch", response_model=JobPublic, status_code=202)
def launch(_: WatchAction, settings: Annotated[Settings, Depends(get_settings)]) -> JobPublic:
    if settings.app.offline_mode:
        raise HTTPException(
            status_code=409, detail="Désactivez le mode hors ligne pour lancer la veille."
        )
    try:
        job = WatchStore(settings).enqueue(force=True)
    except (ResourceBusyError, EvaluationRunBusyError) as error:
        raise HTTPException(
            status_code=409, detail="Une opération du corpus est déjà active."
        ) from error
    return job.to_public()
