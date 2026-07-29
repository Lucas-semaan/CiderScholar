"""Administrator-only maintenance schedule and explicit queue actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.admin.maintenance_state import (
    MaintenanceSchedule,
    maintenance_schedule,
    record_deferral,
)
from app.api.dependencies import get_settings
from app.api.schemas import ConfirmedAdminAction
from app.config import Settings
from app.corpora import LocalProfile, load_local_profile
from app.jobs.contracts import JobPublic
from app.jobs.repository import JobRepository

router = APIRouter(prefix="/api/admin/maintenance", tags=["admin-maintenance"])


def _admin_profile() -> LocalProfile:
    profile = load_local_profile()
    if profile is not LocalProfile.ADMIN:
        raise HTTPException(status_code=403, detail="Profil administrateur local requis.")
    return profile


def _schedule(request: Request, settings: Settings) -> MaintenanceSchedule:
    return maintenance_schedule(
        settings,
        _admin_profile(),
        deferred_for_launch=bool(request.app.state.admin_maintenance_deferred),
    )


@router.get("", response_model=MaintenanceSchedule)
def status(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MaintenanceSchedule:
    return _schedule(request, settings)


@router.post("/defer", response_model=MaintenanceSchedule)
def defer(
    _: ConfirmedAdminAction,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MaintenanceSchedule:
    record_deferral(settings, _admin_profile())
    request.app.state.admin_maintenance_deferred = True
    return _schedule(request, settings)


@router.post("/launch", response_model=JobPublic)
def launch(
    _: ConfirmedAdminAction,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JobPublic:
    _admin_profile()
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    job = repository.enqueue_weekly_maintenance()
    request.app.state.admin_maintenance_deferred = True
    return job.to_public()
