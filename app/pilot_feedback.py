"""Content-minimal defect intake for the local two-person pilot."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database.sqlite import Database

PilotDefectType = Literal["blocking", "functional", "usability", "performance", "other"]


class PilotDefectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PilotDefectType
    step: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1500)

    @field_validator("step", "description")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must contain visible text")
        return cleaned


class PilotDefect(PilotDefectCreate):
    id: str
    created_at: datetime


class PilotFeedbackRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(self, payload: PilotDefectCreate, *, now: datetime | None = None) -> PilotDefect:
        defect = PilotDefect(
            id=str(uuid4()),
            created_at=(now or datetime.now(UTC)).astimezone(UTC),
            **payload.model_dump(),
        )
        with closing(self.database.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO pilot_defects(id, defect_type, step, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    defect.id,
                    defect.type,
                    defect.step,
                    defect.description,
                    defect.created_at.isoformat(),
                ),
            )
        return defect

    def list_recent(self, *, limit: int = 50) -> list[PilotDefect]:
        bounded_limit = max(1, min(limit, 100))
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, defect_type, step, description, created_at
                FROM pilot_defects
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [
            PilotDefect(
                id=str(row["id"]),
                type=str(row["defect_type"]),
                step=str(row["step"]),
                description=str(row["description"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        ]
