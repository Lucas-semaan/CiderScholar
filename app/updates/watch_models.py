"""Public non-secret configuration and durable checkpoints for bibliographic watch."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.jobs.contracts import JobPublic
from app.updates.harvest import CIDER_PILOT_THEMES


class WatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WatchTheme(WatchModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    query: str = Field(min_length=2, max_length=500)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("theme query must contain at least two characters")
        return cleaned


class WatchConfiguration(WatchModel):
    enabled: bool = False
    themes: list[WatchTheme] = Field(
        default_factory=lambda: [
            WatchTheme(key=key, query=query) for key, query in CIDER_PILOT_THEMES.items()
        ],
        min_length=1,
        max_length=32,
    )

    @field_validator("themes")
    @classmethod
    def unique_keys(cls, values: list[WatchTheme]) -> list[WatchTheme]:
        if len({item.key for item in values}) != len(values):
            raise ValueError("theme keys must be unique")
        return values


class WatchCursor(WatchModel):
    offset: int = Field(default=0, ge=0)
    since: str | None = None
    until: str | None = None
    retry_at: datetime | None = None
    no_gain_rotations: int = 0
    rotation_gain: int = 0


class WatchReport(WatchModel):
    job_id: str
    state: Literal["running", "completed", "partial", "failed", "cancelled"] = "running"
    started_at: datetime
    completed_at: datetime | None = None
    examined: int = 0
    duplicates: int = 0
    accepted: int = 0
    review: int = 0
    rejected: int = 0
    acquisitions_attempted: int = 0
    added_to_rag: int = 0
    full_articles: int = 0
    abstracts_only: int = 0
    deferred: int = 0
    errors: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class WatchCheckpoint(WatchModel):
    config: WatchConfiguration
    report: WatchReport
    elapsed_seconds: float = 0.0
    harvest_run_id: str | None = None
    backup_done: bool = False
    harvest_done: bool = False
    record_ids: list[str] = Field(default_factory=list)
    completed_record_ids: list[str] = Field(default_factory=list)
    acquisition_record_ids: list[str] = Field(default_factory=list)
    lane_counts: dict[str, int] = Field(default_factory=dict)


class WatchStatus(WatchModel):
    configuration: WatchConfiguration
    next_due_at: datetime | None
    active_job: JobPublic | None
    history: list[WatchReport]
    suspended_reason: str | None = None


class WatchAction(WatchModel):
    pass
