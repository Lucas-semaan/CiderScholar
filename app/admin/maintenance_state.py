"""Persistent administrator maintenance cadence and success metadata."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings
from app.corpora import LocalProfile

MAINTENANCE_INTERVAL = timedelta(days=7)


class MaintenanceStateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuccessfulMaintenance(MaintenanceStateModel):
    schema_version: Literal[1] = 1
    completed_at: datetime
    corpus_version: str = Field(pattern=r"^corpus-v1-[0-9a-f]{64}$")
    result: Literal["published"] = "published"
    job_id: UUID

    @field_validator("completed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("maintenance completion must be timezone-aware")
        return value


class MaintenanceDeferral(MaintenanceStateModel):
    schema_version: Literal[1] = 1
    deferred_at: datetime


class MaintenanceSchedule(MaintenanceStateModel):
    administrator: Literal[True] = True
    due: bool
    prompt: bool
    next_due_at: datetime | None
    last_success: SuccessfulMaintenance | None
    last_deferred_at: datetime | None


def maintenance_state_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "admin" / "maintenance-success.json"


def maintenance_deferral_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "admin" / "maintenance-deferral.json"


def _require_admin(profile: LocalProfile) -> None:
    if profile is not LocalProfile.ADMIN:
        raise PermissionError("La maintenance est réservée au profil administrateur local.")


def _atomic_model_write(destination: Path, model: MaintenanceStateModel) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def read_last_success(settings: Settings) -> SuccessfulMaintenance | None:
    path = maintenance_state_path(settings)
    if not path.is_file():
        return None
    return SuccessfulMaintenance.model_validate_json(path.read_bytes())


def read_last_deferral(settings: Settings) -> MaintenanceDeferral | None:
    path = maintenance_deferral_path(settings)
    if not path.is_file():
        return None
    return MaintenanceDeferral.model_validate_json(path.read_bytes())


def maintenance_schedule(
    settings: Settings,
    profile: LocalProfile,
    *,
    now: datetime | None = None,
    deferred_for_launch: bool = False,
) -> MaintenanceSchedule:
    _require_admin(profile)
    checked_at = now or datetime.now(UTC)
    success = read_last_success(settings)
    deferral = read_last_deferral(settings)
    next_due_at = success.completed_at + MAINTENANCE_INTERVAL if success else None
    due = next_due_at is None or checked_at >= next_due_at
    return MaintenanceSchedule(
        due=due,
        prompt=due and not deferred_for_launch,
        next_due_at=next_due_at,
        last_success=success,
        last_deferred_at=deferral.deferred_at if deferral else None,
    )


def record_success(
    settings: Settings,
    profile: LocalProfile,
    *,
    corpus_version: str,
    job_id: UUID,
    completed_at: datetime | None = None,
) -> SuccessfulMaintenance:
    _require_admin(profile)
    state = SuccessfulMaintenance(
        completed_at=completed_at or datetime.now(UTC),
        corpus_version=corpus_version,
        job_id=job_id,
    )
    _atomic_model_write(maintenance_state_path(settings), state)
    return state


def record_deferral(
    settings: Settings,
    profile: LocalProfile,
    *,
    deferred_at: datetime | None = None,
) -> MaintenanceDeferral:
    _require_admin(profile)
    deferral = MaintenanceDeferral(deferred_at=deferred_at or datetime.now(UTC))
    _atomic_model_write(maintenance_deferral_path(settings), deferral)
    return deferral
