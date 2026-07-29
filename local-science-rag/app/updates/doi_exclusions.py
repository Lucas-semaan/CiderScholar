"""Persistent DOI exclusions preventing rejected notices from being harvested again."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.updates.models import normalize_doi

DOI_EXCLUSIONS_FILENAME = "excluded_bibliographic_dois.json"
_REGISTRY_LOCK = RLock()
_REGISTRY_CACHE: dict[
    Path,
    tuple[tuple[int, int], DoiExclusionDocument, set[str]],
] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DoiExclusionEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doi: str
    title: str | None = None
    active: bool = True
    first_excluded_at: datetime
    last_excluded_at: datetime
    exclusion_count: int = Field(default=1, ge=1)
    reasons: list[str] = Field(default_factory=list)
    origins: list[str] = Field(default_factory=list)
    reinstated_at: datetime | None = None

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, value: str) -> str:
        normalized = normalize_doi(value)
        if normalized is None:
            raise ValueError("excluded DOI is invalid")
        return normalized


class DoiExclusionDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    updated_at: datetime = Field(default_factory=_utc_now)
    entries: list[DoiExclusionEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_dois(self) -> DoiExclusionDocument:
        dois = [entry.doi for entry in self.entries]
        if len(dois) != len(set(dois)):
            raise ValueError("DOI exclusion registry contains duplicate entries")
        return self


class DoiExclusionRegistry:
    """Read and atomically update one human-readable DOI exclusion file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache_key = path.resolve()
        self._cached_document: DoiExclusionDocument | None = None
        self._cached_signature: tuple[int, int] | None = None
        self._cached_active_dois: set[str] = set()

    @classmethod
    def for_database(cls, database_path: Path) -> DoiExclusionRegistry:
        return cls(database_path.parent / DOI_EXCLUSIONS_FILENAME)

    def is_excluded(self, doi: str | None) -> bool:
        normalized = normalize_doi(doi)
        if normalized is None:
            return False
        with _REGISTRY_LOCK:
            self._read()
            return normalized in self._cached_active_dois

    def exclude(
        self,
        doi: str | None,
        *,
        title: str | None,
        reason: str | None,
        origin: str,
        excluded_at: datetime | None = None,
    ) -> bool:
        return bool(
            self.exclude_many(
                [
                    {
                        "doi": doi,
                        "title": title,
                        "reason": reason,
                        "origin": origin,
                        "excluded_at": excluded_at,
                    }
                ]
            )
        )

    def exclude_many(self, records: Iterable[dict[str, object]]) -> int:
        """Record new exclusion events; repeated active events remain idempotent."""

        with _REGISTRY_LOCK:
            document = self._read().model_copy(deep=True)
            entries = {entry.doi: entry for entry in document.entries}
            changed = 0
            write_required = False
            for record in records:
                normalized = normalize_doi(record.get("doi"))
                if normalized is None:
                    continue
                title = self._clean_optional_text(record.get("title"), 1000)
                reason = self._clean_optional_text(record.get("reason"), 2000)
                origin = self._clean_required_text(record.get("origin"), 100)
                occurred_at = self._coerce_datetime(record.get("excluded_at"))
                entry = entries.get(normalized)
                if entry is None:
                    entries[normalized] = DoiExclusionEntry(
                        doi=normalized,
                        title=title,
                        first_excluded_at=occurred_at,
                        last_excluded_at=occurred_at,
                        reasons=[reason] if reason else [],
                        origins=[origin],
                    )
                    changed += 1
                    write_required = True
                    continue
                if title and title != entry.title:
                    entry.title = title
                    write_required = True
                if reason and reason not in entry.reasons:
                    entry.reasons.append(reason)
                    write_required = True
                if origin not in entry.origins:
                    entry.origins.append(origin)
                    write_required = True
                if not entry.active:
                    entry.active = True
                    entry.last_excluded_at = occurred_at
                    entry.exclusion_count += 1
                    entry.reinstated_at = None
                    changed += 1
                    write_required = True
            if write_required:
                document.entries = sorted(entries.values(), key=lambda entry: entry.doi)
                self._write(document)
            return changed

    def ensure_historical(self, records: Iterable[dict[str, object]]) -> int:
        """Import missing archive rows without reactivating explicitly reinstated DOI."""

        with _REGISTRY_LOCK:
            document = self._read().model_copy(deep=True)
            known = {entry.doi for entry in document.entries}
            additions: list[DoiExclusionEntry] = []
            for record in records:
                normalized = normalize_doi(record.get("doi"))
                if normalized is None or normalized in known:
                    continue
                occurred_at = self._coerce_datetime(record.get("excluded_at"))
                reason = self._clean_optional_text(record.get("reason"), 2000)
                additions.append(
                    DoiExclusionEntry(
                        doi=normalized,
                        title=self._clean_optional_text(record.get("title"), 1000),
                        first_excluded_at=occurred_at,
                        last_excluded_at=occurred_at,
                        reasons=[reason] if reason else [],
                        origins=["historical_rejected_archive"],
                    )
                )
                known.add(normalized)
            if additions:
                document.entries = sorted(
                    [*document.entries, *additions], key=lambda entry: entry.doi
                )
                self._write(document)
            return len(additions)

    def reinstate(self, doi: str) -> bool:
        """Explicitly allow one DOI to be harvested again while preserving its history."""

        normalized = normalize_doi(doi)
        if normalized is None:
            raise ValueError("DOI à réautoriser invalide.")
        return bool(self.reinstate_many([normalized]))

    def reinstate_many(self, dois: Iterable[str]) -> int:
        """Reinstate several DOI values with one atomic registry update."""

        normalized_dois = set()
        for doi in dois:
            normalized = normalize_doi(doi)
            if normalized is None:
                raise ValueError("DOI à réautoriser invalide.")
            normalized_dois.add(normalized)
        with _REGISTRY_LOCK:
            document = self._read().model_copy(deep=True)
            reinstated = 0
            reinstated_at = _utc_now()
            for entry in document.entries:
                if entry.doi not in normalized_dois or not entry.active:
                    continue
                entry.active = False
                entry.reinstated_at = reinstated_at
                reinstated += 1
            if reinstated:
                self._write(document)
            return reinstated

    def document(self) -> DoiExclusionDocument:
        with _REGISTRY_LOCK:
            return self._read().model_copy(deep=True)

    def _read(self) -> DoiExclusionDocument:
        if not self.path.exists():
            missing_signature = (-1, -1)
            if self._cached_document is None or self._cached_signature != missing_signature:
                self._set_cache(DoiExclusionDocument(), missing_signature)
            assert self._cached_document is not None
            return self._cached_document
        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._cached_document is not None and self._cached_signature == signature:
            return self._cached_document
        shared = _REGISTRY_CACHE.get(self._cache_key)
        if shared is not None and shared[0] == signature:
            self._cached_signature, self._cached_document, self._cached_active_dois = shared
            return self._cached_document
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            document = DoiExclusionDocument.model_validate(payload)
            self._set_cache(document, signature)
            return document
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Registre DOI illisible : {self.path}") from exc

    def _write(self, document: DoiExclusionDocument) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document.updated_at = _utc_now()
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        stat = self.path.stat()
        self._set_cache(document, (stat.st_mtime_ns, stat.st_size))

    def _set_cache(
        self,
        document: DoiExclusionDocument,
        signature: tuple[int, int],
    ) -> None:
        self._cached_document = document
        self._cached_signature = signature
        self._cached_active_dois = {entry.doi for entry in document.entries if entry.active}
        _REGISTRY_CACHE[self._cache_key] = (
            signature,
            document,
            self._cached_active_dois,
        )

    @staticmethod
    def _clean_optional_text(value: object, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.split())
        return cleaned[:limit] or None

    @classmethod
    def _clean_required_text(cls, value: object, limit: int) -> str:
        cleaned = cls._clean_optional_text(value, limit)
        if cleaned is None:
            raise ValueError("DOI exclusion origin cannot be empty")
        return cleaned

    @staticmethod
    def _coerce_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return _utc_now()
        else:
            return _utc_now()
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
