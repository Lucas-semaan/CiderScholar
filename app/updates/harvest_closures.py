"""Persistent weekly closures for unproductive bibliographic source/query profiles."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.resource_lock import ResourceFileLock

WEEKLY_CLOSURE_DAYS = 7


class WeeklyHarvestClosureRegistry:
    """Keep operational no-gain closures outside scientific SQLite authority."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    def active(self, profile: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        with ResourceFileLock(self.lock_path):
            payload = self._load()
            entry = payload["profiles"].get(profile)
            if not isinstance(entry, dict):
                return None
            closed_until = _parse_utc(entry.get("closed_until"))
            if closed_until is None or closed_until <= observed_at:
                payload["profiles"].pop(profile, None)
                self._write(payload)
                return None
            return dict(entry)

    def close_weekly(
        self,
        *,
        profile: str,
        source: str,
        query_set: str,
        reason: str,
        consecutive_no_gain_runs: int,
        closed_at: datetime | None = None,
    ) -> dict[str, Any]:
        observed_at = (closed_at or datetime.now(UTC)).astimezone(UTC)
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("weekly harvest closure reason is required")
        if consecutive_no_gain_runs < 1:
            raise ValueError("weekly harvest closure requires at least one no-gain run")
        entry = {
            "profile": profile,
            "source": source,
            "query_set": query_set,
            "state": "closed_weekly",
            "reason": normalized_reason,
            "consecutive_no_gain_runs": consecutive_no_gain_runs,
            "closed_at": observed_at.isoformat(),
            "closed_until": (observed_at + timedelta(days=WEEKLY_CLOSURE_DAYS)).isoformat(),
        }
        with ResourceFileLock(self.lock_path):
            payload = self._load()
            payload["profiles"][profile] = entry
            payload["updated_at"] = datetime.now(UTC).isoformat()
            self._write(payload)
        return dict(entry)

    def clear(self, profile: str) -> bool:
        with ResourceFileLock(self.lock_path):
            payload = self._load()
            removed = payload["profiles"].pop(profile, None) is not None
            if removed:
                payload["updated_at"] = datetime.now(UTC).isoformat()
                self._write(payload)
            return removed

    def active_entries(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        with ResourceFileLock(self.lock_path):
            payload = self._load()
            active: list[dict[str, Any]] = []
            expired: list[str] = []
            for profile, entry in payload["profiles"].items():
                if not isinstance(entry, dict):
                    expired.append(profile)
                    continue
                closed_until = _parse_utc(entry.get("closed_until"))
                if closed_until is None or closed_until <= observed_at:
                    expired.append(profile)
                else:
                    active.append(dict(entry))
            for profile in expired:
                payload["profiles"].pop(profile, None)
            if expired:
                payload["updated_at"] = datetime.now(UTC).isoformat()
                self._write(payload)
        return sorted(active, key=lambda entry: (entry["closed_until"], entry["profile"]))

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "profiles": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("profiles"), dict)
        ):
            raise ValueError(f"weekly harvest closure registry is invalid: {self.path}")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def weekly_harvest_closure_path(common_dir: Path) -> Path:
    return common_dir.resolve() / "bibliographic-weekly-closures.json"


def _parse_utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
