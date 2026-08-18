"""DOI-first full-text discovery, bounded download, and PDF ingestion."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.database.sqlite import Database
from app.ingestion.deduplication import sha256_file
from app.ingestion.pipeline import IngestionPipeline, PdfCatalogMetadata
from app.updates.models import normalize_doi

AssetState = Literal[
    "available",
    "authentication_required",
    "unavailable",
    "downloading",
    "downloaded",
    "ingested",
    "failed",
]
NativeFullTextFormat = Literal[
    "jats_xml",
    "tei_xml",
    "structured_xml",
    "cleaned_text",
    "plain_text",
]
NativeAssetState = Literal[
    "available",
    "authentication_required",
    "downloading",
    "downloaded",
    "failed",
]
ProgressCallback = Callable[[str], None]


class FullTextApiError(RuntimeError):
    """An official full-text discovery API could not complete a request."""


class ProviderDeferred(FullTextApiError):
    """A provider or host must not be contacted again before its reset time."""

    def __init__(
        self,
        message: str,
        *,
        retry_at: datetime,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_at = retry_at.astimezone(UTC)
        self.status_code = status_code


class UnsafeFullTextUrl(ValueError):
    """A provider returned a URL that is unsafe for a server-side download."""


class FullTextCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doi: str
    source: str
    provider_id: str | None = None
    url: str
    media_type: str = "application/pdf"
    license: str | None = None
    requires_authentication: bool = False


class NativeFullTextCandidate(BaseModel):
    """A provider-native article body retained without pretending it has PDF pages."""

    model_config = ConfigDict(extra="forbid")

    doi: str
    source: str
    format: NativeFullTextFormat
    provider_id: str | None = None
    url: str
    media_type: str
    license: str | None = None
    requires_authentication: bool = False


class FullTextObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    state: Literal["available", "authentication_required", "unavailable"]
    candidate: FullTextCandidate | None = None
    native_candidates: list[NativeFullTextCandidate] = Field(default_factory=list)
    reason: str | None = None


class FullTextAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    doi: str
    title: str
    relevance_status: str
    relevance_theme: str | None = None
    observations: list[FullTextObservation]
    selected: FullTextCandidate | None = None


class FullTextAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doi_count: int = Field(ge=0)
    accepted_doi_count: int = Field(ge=0)
    resolved_count: int = Field(ge=0)
    resolved_accepted_count: int = Field(ge=0)
    source_available_counts: dict[str, int]
    source_authentication_required_counts: dict[str, int]
    source_errors: dict[str, str]
    records: list[FullTextAuditRecord]


class FullTextHarvestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audited_dois: int = Field(ge=0)
    resolved_dois: int = Field(ge=0)
    accepted_candidates: int = Field(ge=0)
    already_ingested: int = Field(ge=0)
    previously_failed: int = Field(ge=0)
    downloaded: int = Field(ge=0)
    ingested: int = Field(ge=0)
    duplicate: int = Field(ge=0)
    ocr_required: int = Field(ge=0)
    deferred: int = Field(ge=0)
    failed: int = Field(ge=0)
    article_ids: list[str]
    errors: list[dict[str, str]]
    native_downloaded: int = Field(default=0, ge=0)
    native_already_downloaded: int = Field(default=0, ge=0)
    native_deferred: int = Field(default=0, ge=0)
    native_failed: int = Field(default=0, ge=0)


class DownloadedFullText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    final_url: str
    sha256: str
    byte_count: int = Field(gt=0)
    media_type: str


class FullTextStore:
    """Persist resolution provenance without mixing it into bibliography metadata."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def doi_records(self) -> list[dict[str, Any]]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, lower(doi) AS doi, title, abstract, authors, journal,
                       publication_year, relevance_status, relevance_theme
                FROM bibliographic_records
                WHERE doi IS NOT NULL AND trim(doi) != ''
                ORDER BY lower(doi)
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def cached_observations(
        self,
        *,
        max_age_hours: int,
        sources: Sequence[str],
    ) -> dict[str, dict[str, FullTextObservation]]:
        """Reuse recent provider answers so recurring runs only audit new/stale DOI values."""

        if not sources:
            return {}
        placeholders = ",".join("?" for _ in sources)
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT doi, source, provider_id, source_url, media_type, license,
                       state, error_message
                FROM full_text_assets
                WHERE source IN ({placeholders})
                  AND checked_at >= datetime('now', ?)
                """,
                (*sources, f"-{max_age_hours} hours"),
            ).fetchall()
            native_rows = connection.execute(
                f"""
                SELECT doi, source, format, provider_id, source_url, media_type, license, state
                FROM native_full_text_assets
                WHERE source IN ({placeholders})
                  AND checked_at >= datetime('now', ?)
                """,
                (*sources, f"-{max_age_hours} hours"),
            ).fetchall()
        native_candidates: dict[tuple[str, str], list[NativeFullTextCandidate]] = {}
        for row in native_rows:
            state = str(row["state"])
            source_url = str(row["source_url"] or "").strip()
            if not source_url or state not in {
                "available",
                "authentication_required",
                "downloading",
                "downloaded",
            }:
                continue
            doi = str(row["doi"])
            source = str(row["source"])
            native_candidates.setdefault((doi, source), []).append(
                NativeFullTextCandidate(
                    doi=doi,
                    source=source,
                    format=str(row["format"]),
                    provider_id=(str(row["provider_id"]) if row["provider_id"] else None),
                    url=source_url,
                    media_type=str(row["media_type"]),
                    license=(str(row["license"]) if row["license"] else None),
                    requires_authentication=state == "authentication_required",
                )
            )
        cached: dict[str, dict[str, FullTextObservation]] = {}
        for row in rows:
            doi = str(row["doi"])
            source = str(row["source"])
            state = str(row["state"])
            source_url = str(row["source_url"] or "").strip()
            candidate = None
            if source_url and state in {
                "available",
                "authentication_required",
                "downloading",
                "downloaded",
                "ingested",
            }:
                candidate = FullTextCandidate(
                    doi=doi,
                    source=source,
                    provider_id=(str(row["provider_id"]) if row["provider_id"] else None),
                    url=source_url,
                    media_type=str(row["media_type"] or "application/pdf"),
                    license=(str(row["license"]) if row["license"] else None),
                    requires_authentication=state == "authentication_required",
                )
            source_native_candidates = native_candidates.pop((doi, source), [])
            if state == "authentication_required" or (
                source_native_candidates
                and all(candidate.requires_authentication for candidate in source_native_candidates)
            ):
                observation_state = "authentication_required"
            elif candidate is not None or source_native_candidates:
                observation_state = "available"
            else:
                observation_state = "unavailable"
            cached.setdefault(doi, {})[source] = FullTextObservation(
                source=source,
                state=observation_state,
                candidate=candidate,
                native_candidates=source_native_candidates,
                reason=(str(row["error_message"]) if row["error_message"] else None),
            )
        for (doi, source), source_native_candidates in native_candidates.items():
            state = (
                "authentication_required"
                if all(candidate.requires_authentication for candidate in source_native_candidates)
                else "available"
            )
            cached.setdefault(doi, {})[source] = FullTextObservation(
                source=source,
                state=state,
                native_candidates=source_native_candidates,
            )
        return cached

    def active_cooldown(
        self,
        source: str,
        *,
        host: str | None = None,
    ) -> dict[str, Any] | None:
        scopes = [f"source:{source}"]
        if host:
            scopes.append(f"host:{host.casefold()}")
        placeholders = ",".join("?" for _ in scopes)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                f"""
                SELECT * FROM full_text_provider_cooldowns
                WHERE scope IN ({placeholders})
                  AND retry_at > strftime('%Y-%m-%d %H:%M:%S', 'now')
                ORDER BY retry_at DESC
                LIMIT 1
                """,
                scopes,
            ).fetchone()
        return dict(row) if row is not None else None

    def set_cooldown(
        self,
        source: str,
        *,
        retry_at: datetime,
        reason: str,
        status_code: int | None = None,
        host: str | None = None,
    ) -> None:
        normalized_host = host.casefold() if host else None
        scope = f"host:{normalized_host}" if normalized_host else f"source:{source}"
        retry_text = retry_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO full_text_provider_cooldowns (
                    scope, source, host, reason, http_status, retry_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    source = excluded.source,
                    host = excluded.host,
                    reason = excluded.reason,
                    http_status = excluded.http_status,
                    retry_at = CASE
                        WHEN excluded.retry_at > full_text_provider_cooldowns.retry_at
                        THEN excluded.retry_at
                        ELSE full_text_provider_cooldowns.retry_at
                    END,
                    failure_count = full_text_provider_cooldowns.failure_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (scope, source, normalized_host, reason[:500], status_code, retry_text),
            )

    def upsert_observation(
        self,
        record: Mapping[str, Any],
        observation: FullTextObservation,
    ) -> None:
        self.upsert_observations([(record, observation)])

    def upsert_observations(
        self,
        values: Sequence[tuple[Mapping[str, Any], FullTextObservation]],
    ) -> None:
        if not values:
            return
        rows: list[tuple[Any, ...]] = []
        native_rows: list[tuple[Any, ...]] = []
        for record, observation in values:
            candidate = observation.candidate
            asset_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ciderscholar:full-text:{record['id']}:{observation.source}",
                )
            )
            rows.append(
                (
                    asset_id,
                    record["id"],
                    record["doi"],
                    observation.source,
                    candidate.provider_id if candidate else None,
                    candidate.url if candidate else None,
                    candidate.media_type if candidate else None,
                    candidate.license if candidate else None,
                    observation.state,
                    observation.reason,
                )
            )
            for native in observation.native_candidates:
                native_rows.append(
                    (
                        str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                "ciderscholar:native-full-text:"
                                f"{record['id']}:{native.source}:{native.format}",
                            )
                        ),
                        record["id"],
                        record["doi"],
                        native.source,
                        native.format,
                        native.provider_id,
                        native.url,
                        native.media_type,
                        native.license,
                        (
                            "authentication_required"
                            if native.requires_authentication
                            else "available"
                        ),
                    )
                )
        with self.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO full_text_assets (
                    id, record_id, doi, source, provider_id, source_url,
                    media_type, license, state, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id, source) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    source_url = excluded.source_url,
                    media_type = excluded.media_type,
                    license = excluded.license,
                    state = CASE
                        WHEN full_text_assets.state = 'ingested' THEN 'ingested'
                        WHEN full_text_assets.state = 'failed'
                             AND full_text_assets.source_url = excluded.source_url
                             AND COALESCE(full_text_assets.error_type, '') NOT IN (
                                 'ReadTimeout', 'ConnectTimeout', 'ConnectError'
                             )
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 429%'
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 500%'
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 502%'
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 503%'
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 504%'
                        THEN 'failed'
                        ELSE excluded.state
                    END,
                    error_type = CASE
                        WHEN full_text_assets.state = 'failed'
                             AND full_text_assets.source_url = excluded.source_url
                             AND COALESCE(full_text_assets.error_type, '') NOT IN (
                                 'ReadTimeout', 'ConnectTimeout', 'ConnectError'
                             )
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 429%'
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 50_%'
                        THEN full_text_assets.error_type ELSE NULL END,
                    error_message = CASE
                        WHEN full_text_assets.state = 'failed'
                             AND full_text_assets.source_url = excluded.source_url
                             AND COALESCE(full_text_assets.error_type, '') NOT IN (
                                 'ReadTimeout', 'ConnectTimeout', 'ConnectError'
                             )
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 429%'
                             AND COALESCE(full_text_assets.error_message, '')
                                 NOT LIKE '%HTTP 50_%'
                        THEN full_text_assets.error_message ELSE excluded.error_message END,
                    checked_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
            if native_rows:
                connection.executemany(
                    """
                    INSERT INTO native_full_text_assets (
                        id, record_id, doi, source, format, provider_id, source_url,
                        media_type, license, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(record_id, source, format) DO UPDATE SET
                        provider_id = excluded.provider_id,
                        source_url = excluded.source_url,
                        media_type = excluded.media_type,
                        license = excluded.license,
                        state = CASE
                            WHEN native_full_text_assets.state = 'downloaded' THEN 'downloaded'
                            WHEN native_full_text_assets.state = 'failed'
                                 AND native_full_text_assets.source_url = excluded.source_url
                            THEN 'failed'
                            ELSE excluded.state
                        END,
                        checked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    native_rows,
                )

    def failed_candidate_keys(self) -> set[tuple[str, str]]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT record_id, source FROM full_text_assets
                WHERE state = 'failed'
                  AND COALESCE(error_type, '') NOT IN (
                      'ReadTimeout', 'ConnectTimeout', 'ConnectError'
                  )
                  AND COALESCE(error_message, '') NOT LIKE '%HTTP 429%'
                  AND COALESCE(error_message, '') NOT LIKE '%HTTP 50_%'
                """
            ).fetchall()
        return {(str(row["record_id"]), str(row["source"])) for row in rows}

    def downloaded_native_candidate_keys(self) -> set[tuple[str, str, str]]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT record_id, source, format FROM native_full_text_assets
                WHERE state = 'downloaded' AND file_path IS NOT NULL AND sha256 IS NOT NULL
                """
            ).fetchall()
        return {(str(row["record_id"]), str(row["source"]), str(row["format"])) for row in rows}

    def failed_native_candidate_keys(self) -> set[tuple[str, str, str]]:
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT record_id, source, format FROM native_full_text_assets
                WHERE state = 'failed'
                  AND COALESCE(error_type, '') NOT IN (
                      'ReadTimeout', 'ConnectTimeout', 'ConnectError'
                  )
                  AND COALESCE(error_message, '') NOT LIKE '%HTTP 429%'
                  AND COALESCE(error_message, '') NOT LIKE '%HTTP 50_%'
                """
            ).fetchall()
        return {(str(row["record_id"]), str(row["source"]), str(row["format"])) for row in rows}

    def update_asset(
        self,
        *,
        record_id: str,
        source: str,
        state: AssetState,
        article_id: str | None = None,
        downloaded: DownloadedFullText | None = None,
        error: Exception | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE full_text_assets
                SET state = ?, article_id = COALESCE(?, article_id),
                    final_url = COALESCE(?, final_url),
                    file_path = COALESCE(?, file_path),
                    sha256 = COALESCE(?, sha256),
                    byte_count = COALESCE(?, byte_count),
                    media_type = COALESCE(?, media_type),
                    error_type = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE record_id = ? AND source = ?
                """,
                (
                    state,
                    article_id,
                    downloaded.final_url if downloaded else None,
                    str(downloaded.path) if downloaded else None,
                    downloaded.sha256 if downloaded else None,
                    downloaded.byte_count if downloaded else None,
                    downloaded.media_type if downloaded else None,
                    type(error).__name__ if error else None,
                    str(error)[:500] if error else None,
                    record_id,
                    source,
                ),
            )

    def update_native_asset(
        self,
        *,
        record_id: str,
        candidate: NativeFullTextCandidate,
        state: NativeAssetState,
        downloaded: DownloadedFullText | None = None,
        error: Exception | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE native_full_text_assets
                SET state = ?, final_url = COALESCE(?, final_url),
                    file_path = COALESCE(?, file_path), sha256 = COALESCE(?, sha256),
                    byte_count = COALESCE(?, byte_count), media_type = COALESCE(?, media_type),
                    error_type = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE record_id = ? AND source = ? AND format = ?
                """,
                (
                    state,
                    downloaded.final_url if downloaded else None,
                    str(downloaded.path) if downloaded else None,
                    downloaded.sha256 if downloaded else None,
                    downloaded.byte_count if downloaded else None,
                    downloaded.media_type if downloaded else None,
                    type(error).__name__ if error else None,
                    str(error)[:500] if error else None,
                    record_id,
                    candidate.source,
                    candidate.format,
                ),
            )


class OfficialFullTextClient:
    """Shared retry, pacing, and no-redirect behavior for official APIs."""

    def __init__(
        self,
        settings: Settings,
        *,
        store: FullTextStore | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.config = settings.full_text
        self.store = store
        self._http = httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "CiderScholar/2.0"},
        )
        self._last_request_at: dict[str, float] = {}

    def _pace(self, source: str) -> None:
        delay = self.config.request_delay_seconds
        if source == "core":
            delay = max(delay, self.config.core_request_delay_seconds)
        elif source in {"hal", "semantic_scholar", "doaj"}:
            delay = max(delay, self.config.repository_request_delay_seconds)
        remaining = self._last_request_at.get(source, 0.0) + delay - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at[source] = time.monotonic()

    def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        not_found_is_empty: bool = False,
    ) -> dict[str, Any]:
        return self._request_json(
            "GET",
            url,
            params=params,
            headers=headers,
            not_found_is_empty=not_found_is_empty,
        )

    def _post_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any],
    ) -> dict[str, Any] | list[Any]:
        return self._request_json(
            "POST",
            url,
            params=params,
            headers=headers,
            json_body=json_body,
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        not_found_is_empty: bool = False,
    ) -> dict[str, Any] | list[Any]:
        source = self._source_for_url(url)
        if self.store is not None:
            cooldown = self.store.active_cooldown(source)
            if cooldown is not None:
                retry_at = _parse_sqlite_utc(str(cooldown["retry_at"]))
                raise ProviderDeferred(
                    f"{source} différé jusqu'au {retry_at.isoformat()}",
                    retry_at=retry_at,
                    status_code=cooldown.get("http_status"),
                )
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self._pace(source)
            try:
                response = self._http.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json_body,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.config.max_retries:
                    time.sleep(2**attempt)
                    continue
                break
            if response.status_code == 404 and not_found_is_empty:
                return {}
            if response.status_code == 429 or (
                response.status_code == 503 and response.headers.get("Retry-After")
            ):
                retry_at = _retry_at_from_headers(
                    response.headers,
                    default_hours=self.config.default_rate_limit_cooldown_hours,
                )
                error = ProviderDeferred(
                    f"{source} returned HTTP {response.status_code}; retry after "
                    f"{retry_at.isoformat()}",
                    retry_at=retry_at,
                    status_code=response.status_code,
                )
                if self.store is not None:
                    self.store.set_cooldown(
                        source,
                        retry_at=retry_at,
                        reason=str(error),
                        status_code=response.status_code,
                    )
                raise error
            if response.status_code in {500, 502, 503, 504} and attempt < self.config.max_retries:
                time.sleep(max(self.config.request_delay_seconds, 2**attempt))
                continue
            if response.is_redirect:
                raise FullTextApiError("official API returned an unexpected redirect")
            if response.is_error:
                raise FullTextApiError(f"official API returned HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise FullTextApiError("official API returned invalid JSON") from exc
            if not isinstance(payload, (dict, list)):
                raise FullTextApiError("official API returned an unexpected JSON structure")
            return payload
        error = FullTextApiError("official API remained unavailable after bounded retries")
        if self.store is not None:
            hours = (
                self.config.timeout_cooldown_hours
                if isinstance(last_error, httpx.TimeoutException)
                else self.config.default_rate_limit_cooldown_hours
            )
            self.store.set_cooldown(
                source,
                retry_at=datetime.now(UTC) + timedelta(hours=hours),
                reason=f"{type(last_error).__name__}: {last_error}"[:500],
            )
        raise error from last_error

    def _source_for_url(self, url: str) -> str:
        for source, base_url in (
            ("europe_pmc", self.config.europe_pmc_base_url),
            ("istex", self.config.istex_base_url),
            ("core", self.config.core_base_url),
            ("hal", self.config.hal_base_url),
            ("semantic_scholar", self.config.semantic_scholar_base_url),
            ("openalex", self.config.openalex_base_url),
            ("unpaywall", self.config.unpaywall_base_url),
            ("doaj", self.config.doaj_base_url),
            ("crossref", self.config.crossref_base_url),
            ("elsevier", self.config.elsevier_article_base_url),
        ):
            if url.startswith(base_url.rstrip("/")):
                return source
        return (urlsplit(url).hostname or "unknown").casefold()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OfficialFullTextClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class FullTextAuditService(OfficialFullTextClient):
    """Resolve every stored DOI, stopping a provider after a structural access failure."""

    def audit(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        progress: ProgressCallback | None = None,
        include_slow_fallbacks: bool = True,
        seed_observations: Mapping[str, Mapping[str, FullTextObservation]] | None = None,
        observation_callback: (
            Callable[[Mapping[str, Any], FullTextObservation], None] | None
        ) = None,
        observation_batch_callback: (
            Callable[[Sequence[tuple[Mapping[str, Any], FullTextObservation]]], None] | None
        ) = None,
    ) -> FullTextAuditReport:
        by_doi = {str(record["doi"]): record for record in records}
        dois = list(by_doi)
        observations: dict[str, dict[str, FullTextObservation]] = {
            doi: self._active_seed_observations((seed_observations or {}).get(doi, {}))
            for doi in dois
        }
        source_errors: dict[str, str] = {}

        for source, resolver in (
            ("europe_pmc", self._resolve_europe_pmc),
            ("istex", self._resolve_istex),
            ("core", self._resolve_core),
            ("hal", self._resolve_hal),
            ("semantic_scholar", self._resolve_semantic_scholar),
            ("openalex", self._resolve_openalex),
            ("doaj", self._resolve_doaj),
        ):
            if source not in self.config.sources:
                continue
            pending_dois = [doi for doi in dois if source not in observations[doi]]
            if not pending_dois:
                continue
            if progress:
                progress(f"audit {source}: {len(pending_dois)} DOI à vérifier")
            try:
                resolved = resolver(pending_dois)
                provider_observations: list[tuple[Mapping[str, Any], FullTextObservation]] = []
                for doi in pending_dois:
                    observation = resolved.get(
                        doi,
                        FullTextObservation(source=source, state="unavailable"),
                    )
                    observations[doi][source] = observation
                    provider_observations.append((by_doi[doi], observation))
                if observation_batch_callback is not None:
                    observation_batch_callback(provider_observations)
            except Exception as exc:
                source_errors[source] = f"{type(exc).__name__}: {str(exc)[:300]}"

        unresolved = [doi for doi in dois if not _select_candidate(observations[doi].values())]
        if include_slow_fallbacks and "unpaywall" in self.config.sources:
            pending_dois = [doi for doi in unresolved if "unpaywall" not in observations[doi]]
            if progress:
                progress(f"audit unpaywall: {len(pending_dois)} DOI non résolus")
            self._resolve_slow_source(
                pending_dois,
                "unpaywall",
                self._resolve_unpaywall_one,
                observations,
                source_errors,
                progress,
                by_doi,
                observation_callback,
            )
            unresolved = [
                doi for doi in unresolved if not _select_candidate(observations[doi].values())
            ]
        if include_slow_fallbacks and "crossref" in self.config.sources:
            pending_dois = [doi for doi in unresolved if "crossref" not in observations[doi]]
            if progress:
                progress(f"audit crossref TDM: {len(pending_dois)} DOI non résolus")
            self._resolve_slow_source(
                pending_dois,
                "crossref",
                self._resolve_crossref_one,
                observations,
                source_errors,
                progress,
                by_doi,
                observation_callback,
            )
        if "elsevier" in self.config.sources:
            source_errors["elsevier"] = (
                "Article Retrieval API testé: clé valide, mais réponse limitée aux métadonnées "
                "sans corps d'article ni PDF; source arrêtée sans répétition."
            )

        audit_records: list[FullTextAuditRecord] = []
        available_counts: dict[str, int] = {}
        authentication_counts: dict[str, int] = {}
        for doi in dois:
            values = list(observations[doi].values())
            for observation in values:
                if observation.state == "available":
                    available_counts[observation.source] = (
                        available_counts.get(observation.source, 0) + 1
                    )
                elif observation.state == "authentication_required":
                    authentication_counts[observation.source] = (
                        authentication_counts.get(observation.source, 0) + 1
                    )
            record = by_doi[doi]
            audit_records.append(
                FullTextAuditRecord(
                    record_id=str(record["id"]),
                    doi=doi,
                    title=str(record["title"]),
                    relevance_status=str(record["relevance_status"]),
                    relevance_theme=(
                        str(record["relevance_theme"]) if record.get("relevance_theme") else None
                    ),
                    observations=values,
                    selected=_select_candidate(values),
                )
            )
        return FullTextAuditReport(
            doi_count=len(audit_records),
            accepted_doi_count=sum(
                record.relevance_status == "accepted" for record in audit_records
            ),
            resolved_count=sum(record.selected is not None for record in audit_records),
            resolved_accepted_count=sum(
                record.selected is not None and record.relevance_status == "accepted"
                for record in audit_records
            ),
            source_available_counts=available_counts,
            source_authentication_required_counts=authentication_counts,
            source_errors=source_errors,
            records=audit_records,
        )

    def _active_seed_observations(
        self,
        observations: Mapping[str, FullTextObservation],
    ) -> dict[str, FullTextObservation]:
        """Recheck protected ISTEX assets as soon as a token becomes available."""

        active = dict(observations)
        istex = active.get("istex")
        has_istex_token = bool(os.environ.get(self.config.istex_token_env, "").strip())
        if has_istex_token and istex is not None and istex.state == "authentication_required":
            active.pop("istex")
        return active

    def _resolve_slow_source(
        self,
        dois: list[str],
        source: str,
        resolver: Callable[[str], FullTextObservation],
        observations: dict[str, dict[str, FullTextObservation]],
        source_errors: dict[str, str],
        progress: ProgressCallback | None,
        by_doi: Mapping[str, Mapping[str, Any]],
        observation_callback: (Callable[[Mapping[str, Any], FullTextObservation], None] | None),
    ) -> list[str]:
        consecutive_structural_failures = 0
        remaining: list[str] = []
        for index, doi in enumerate(dois, start=1):
            try:
                observation = resolver(doi)
                observations[doi][source] = observation
                if observation_callback is not None:
                    observation_callback(by_doi[doi], observation)
                consecutive_structural_failures = 0
                if observation.state != "available":
                    remaining.append(doi)
            except Exception as exc:
                consecutive_structural_failures += 1
                remaining.append(doi)
                if consecutive_structural_failures >= 3:
                    source_errors[source] = (
                        f"{type(exc).__name__}: {str(exc)[:300]}; source arrêtée après "
                        "trois échecs structurels consécutifs."
                    )
                    remaining.extend(dois[index:])
                    break
            if progress and (index % 100 == 0 or index == len(dois)):
                progress(f"audit {source}: {index}/{len(dois)}")
        return remaining

    def _resolve_core(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        api_key = os.environ.get(self.config.core_api_key_env, "").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        for batch in _batches(dois, min(50, self.config.batch_size * 2)):
            query = " OR ".join(f'doi:"{doi}"' for doi in batch)
            payload = self._get_json(
                f"{self.config.core_base_url}/search/works/",
                params={"q": query, "limit": min(100, len(batch) * 4)},
                headers=headers,
            )
            hits = payload.get("results")
            for hit in hits if isinstance(hits, list) else []:
                if not isinstance(hit, dict):
                    continue
                doi = normalize_doi(hit.get("doi"))
                url = hit.get("downloadUrl")
                if doi not in batch or not isinstance(url, str) or not url.startswith("https://"):
                    continue
                results[doi] = FullTextObservation(
                    source="core",
                    state="available",
                    candidate=FullTextCandidate(
                        doi=doi,
                        source="core",
                        provider_id=(str(hit.get("id")) if hit.get("id") else None),
                        url=url,
                    ),
                )
        return results

    def _resolve_hal(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        for batch in _batches(dois, min(50, self.config.batch_size * 2)):
            query = " OR ".join(f'doiId_s:"{doi}"' for doi in batch)
            payload = self._get_json(
                f"{self.config.hal_base_url}/",
                params={
                    "q": query,
                    "fq": "submitType_s:file",
                    "fl": "docid,doiId_s,fileMain_s,license_s",
                    "rows": len(batch),
                    "wt": "json",
                },
            )
            response = payload.get("response")
            hits = response.get("docs") if isinstance(response, dict) else None
            for hit in hits if isinstance(hits, list) else []:
                if not isinstance(hit, dict):
                    continue
                doi = normalize_doi(hit.get("doiId_s"))
                url = hit.get("fileMain_s")
                if doi not in batch or not isinstance(url, str) or not url.startswith("https://"):
                    continue
                license_value = hit.get("license_s")
                results[doi] = FullTextObservation(
                    source="hal",
                    state="available",
                    candidate=FullTextCandidate(
                        doi=doi,
                        source="hal",
                        provider_id=(str(hit.get("docid")) if hit.get("docid") else None),
                        url=url,
                        license=(str(license_value) if license_value else None),
                    ),
                )
        return results

    def _resolve_semantic_scholar(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        api_key = os.environ.get(self.config.semantic_scholar_api_key_env, "").strip()
        headers = {"x-api-key": api_key} if api_key else None
        batch_size = min(500, max(100, self.config.batch_size * 10))
        for batch in _batches(dois, batch_size):
            payload = self._post_json(
                f"{self.config.semantic_scholar_base_url}/paper/batch",
                params={"fields": "externalIds,isOpenAccess,openAccessPdf,title"},
                headers=headers,
                json_body={"ids": [f"DOI:{doi}" for doi in batch]},
            )
            for hit in payload if isinstance(payload, list) else []:
                if not isinstance(hit, dict):
                    continue
                external_ids = hit.get("externalIds")
                doi = normalize_doi(
                    external_ids.get("DOI") if isinstance(external_ids, dict) else None
                )
                pdf = hit.get("openAccessPdf")
                url = pdf.get("url") if isinstance(pdf, dict) else None
                hostname = (urlsplit(url).hostname or "").casefold() if isinstance(url, str) else ""
                if (
                    doi not in batch
                    or not hit.get("isOpenAccess")
                    or not isinstance(url, str)
                    or not url.startswith("https://")
                    or hostname in {"doi.org", "dx.doi.org"}
                ):
                    continue
                results[doi] = FullTextObservation(
                    source="semantic_scholar",
                    state="available",
                    candidate=FullTextCandidate(
                        doi=doi,
                        source="semantic_scholar",
                        provider_id=(str(hit.get("paperId")) if hit.get("paperId") else None),
                        url=url,
                        license=(str(pdf.get("license")) if pdf.get("license") else None),
                    ),
                )
        return results

    def _resolve_doaj(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        for batch in _batches(dois, min(20, self.config.batch_size)):
            query = "index.doi.exact:(" + " OR ".join(f'"{doi}"' for doi in batch) + ")"
            encoded_query = quote(query, safe=':()"')
            payload = self._get_json(
                f"{self.config.doaj_base_url}/{encoded_query}",
                params={"pageSize": len(batch)},
            )
            hits = payload.get("results")
            for hit in hits if isinstance(hits, list) else []:
                if not isinstance(hit, dict):
                    continue
                bibjson = hit.get("bibjson")
                if not isinstance(bibjson, dict):
                    continue
                identifiers = bibjson.get("identifier")
                doi = (
                    next(
                        (
                            normalize_doi(value.get("id"))
                            for value in identifiers
                            if isinstance(value, dict) and value.get("type") == "doi"
                        ),
                        None,
                    )
                    if isinstance(identifiers, list)
                    else None
                )
                links = bibjson.get("link")
                pdf = (
                    next(
                        (
                            value
                            for value in links
                            if isinstance(value, dict)
                            and str(value.get("type") or "").casefold() == "fulltext"
                            and str(value.get("content_type") or "").casefold()
                            in {"application/pdf", "pdf"}
                            and isinstance(value.get("url"), str)
                            and str(value["url"]).startswith("https://")
                        ),
                        None,
                    )
                    if isinstance(links, list)
                    else None
                )
                provider_id = str(hit.get("id")) if hit.get("id") else None
                native_candidates = _native_candidates_from_explicit_links(
                    doi,
                    source="doaj",
                    provider_id=provider_id,
                    links=links if isinstance(links, list) else [],
                    url_key="url",
                    media_type_key="content_type",
                    require_fulltext_type=True,
                )
                if doi not in batch or (pdf is None and not native_candidates):
                    continue
                results[doi] = FullTextObservation(
                    source="doaj",
                    state="available",
                    candidate=(
                        FullTextCandidate(
                            doi=doi,
                            source="doaj",
                            provider_id=provider_id,
                            url=str(pdf["url"]),
                        )
                        if pdf is not None
                        else None
                    ),
                    native_candidates=native_candidates,
                )
        return results

    def _resolve_europe_pmc(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        for batch in _batches(dois, self.config.batch_size):
            query = " OR ".join(f"DOI:{doi}" for doi in batch)
            payload = self._get_json(
                f"{self.config.europe_pmc_base_url}/search",
                params={
                    "query": query,
                    "resultType": "core",
                    "pageSize": max(len(batch), 1),
                    "format": "json",
                },
            )
            raw = payload.get("resultList")
            hits = raw.get("result") if isinstance(raw, dict) else None
            for hit in hits if isinstance(hits, list) else []:
                if not isinstance(hit, dict):
                    continue
                doi = normalize_doi(hit.get("doi"))
                if doi not in batch:
                    continue
                candidate = _europe_pmc_candidate(doi, hit)
                native_candidates = _europe_pmc_native_candidates(doi, hit)
                if candidate or native_candidates:
                    results[doi] = FullTextObservation(
                        source="europe_pmc",
                        state="available",
                        candidate=candidate,
                        native_candidates=native_candidates,
                    )
        return results

    def _resolve_istex(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        has_token = bool(os.environ.get(self.config.istex_token_env, "").strip())
        for batch in _batches(dois, self.config.batch_size):
            query = " OR ".join(f'doi:"{doi}"' for doi in batch)
            payload = self._get_json(
                f"{self.config.istex_base_url}/document/",
                params={
                    "q": query,
                    "size": len(batch),
                    "output": "id,title,doi,fulltext",
                },
            )
            hits = payload.get("hits")
            for hit in hits if isinstance(hits, list) else []:
                if not isinstance(hit, dict):
                    continue
                raw_dois = hit.get("doi")
                raw_dois = raw_dois if isinstance(raw_dois, list) else [raw_dois]
                doi = next((normalize_doi(value) for value in raw_dois if value), None)
                if doi not in batch:
                    continue
                assets = hit.get("fulltext")
                pdf = (
                    next(
                        (
                            asset
                            for asset in assets
                            if isinstance(asset, dict) and asset.get("extension") == "pdf"
                        ),
                        None,
                    )
                    if isinstance(assets, list)
                    else None
                )
                if not hit.get("id"):
                    continue
                candidate = (
                    FullTextCandidate(
                        doi=doi,
                        source="istex",
                        provider_id=str(hit["id"]),
                        url=f"{self.config.istex_base_url}/document/{hit['id']}/fulltext/pdf",
                        media_type="application/pdf",
                        requires_authentication=not has_token,
                    )
                    if pdf
                    else None
                )
                native_candidates = _istex_native_candidates(
                    doi,
                    str(hit["id"]),
                    assets if isinstance(assets, list) else [],
                    base_url=self.config.istex_base_url,
                    requires_authentication=not has_token,
                )
                if candidate is None and not native_candidates:
                    continue
                results[doi] = FullTextObservation(
                    source="istex",
                    state="available" if has_token else "authentication_required",
                    candidate=candidate,
                    native_candidates=native_candidates,
                    reason=None if has_token else f"jeton {self.config.istex_token_env} absent",
                )
        return results

    def _resolve_openalex(self, dois: Sequence[str]) -> dict[str, FullTextObservation]:
        results: dict[str, FullTextObservation] = {}
        api_key = os.environ.get(self.config.openalex_api_key_env, "").strip()
        if not api_key:
            raise FullTextApiError(
                f"OpenAlex requires environment variable {self.config.openalex_api_key_env}"
            )
        for batch in _batches(dois, min(100, self.config.batch_size * 4)):
            payload = self._get_json(
                f"{self.config.openalex_base_url}/works",
                params={
                    "filter": f"doi:{'|'.join(batch)}",
                    "per_page": len(batch),
                    "api_key": api_key,
                },
            )
            hits = payload.get("results")
            for hit in hits if isinstance(hits, list) else []:
                if not isinstance(hit, dict):
                    continue
                doi = normalize_doi(hit.get("doi"))
                if doi not in batch:
                    continue
                candidate = _openalex_candidate(doi, hit)
                if candidate:
                    results[doi] = FullTextObservation(
                        source="openalex", state="available", candidate=candidate
                    )
        return results

    def _resolve_unpaywall_one(self, doi: str) -> FullTextObservation:
        email = self.settings.bibliographic.crossref_email.strip()
        if not email:
            raise FullTextApiError("Unpaywall requires the configured Crossref contact email")
        payload = self._get_json(
            f"{self.config.unpaywall_base_url}/{quote(doi, safe='')}",
            params={"email": email},
            not_found_is_empty=True,
        )
        location = payload.get("best_oa_location")
        locations = [location, *(payload.get("oa_locations") or [])]
        for item in locations:
            if not isinstance(item, dict):
                continue
            url = item.get("url_for_pdf")
            if isinstance(url, str) and url.startswith("https://"):
                return FullTextObservation(
                    source="unpaywall",
                    state="available",
                    candidate=FullTextCandidate(
                        doi=doi,
                        source="unpaywall",
                        provider_id=(str(payload.get("doi")) if payload.get("doi") else None),
                        url=url,
                        license=(str(item.get("license")) if item.get("license") else None),
                    ),
                )
        return FullTextObservation(source="unpaywall", state="unavailable")

    def _resolve_crossref_one(self, doi: str) -> FullTextObservation:
        email = self.settings.bibliographic.crossref_email.strip()
        params = {"mailto": email} if email else None
        headers = {
            "User-Agent": f"CiderScholar/2.0 (mailto:{email})" if email else "CiderScholar/2.0"
        }
        payload = self._get_json(
            f"{self.config.crossref_base_url}/works/{quote(doi, safe='')}",
            params=params,
            headers=headers,
            not_found_is_empty=True,
        )
        message = payload.get("message")
        links = message.get("link") if isinstance(message, dict) else None
        pdf_candidate = None
        for link in links if isinstance(links, list) else []:
            if not isinstance(link, dict):
                continue
            media_type = str(link.get("content-type") or "").casefold()
            url = link.get("URL")
            if (
                media_type == "application/pdf"
                and isinstance(url, str)
                and url.startswith("https://")
            ):
                pdf_candidate = FullTextCandidate(
                    doi=doi,
                    source="crossref",
                    provider_id=doi,
                    url=url,
                    media_type="application/pdf",
                )
                break
        native_candidates = _native_candidates_from_explicit_links(
            doi,
            source="crossref",
            provider_id=doi,
            links=links if isinstance(links, list) else [],
            url_key="URL",
            media_type_key="content-type",
        )
        if pdf_candidate or native_candidates:
            return FullTextObservation(
                source="crossref",
                state="available",
                candidate=pdf_candidate,
                native_candidates=native_candidates,
            )
        return FullTextObservation(source="crossref", state="unavailable")


class FullTextDownloader:
    """Download provider-selected PDF or native assets atomically with strict URL checks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.full_text

    def download(self, candidate: FullTextCandidate) -> DownloadedFullText:
        # Provider PDFs are scientific corpus assets, even when this service
        # is invoked by a legacy reconciliation workflow.  Keep them beside
        # native JATS/TEI/text assets instead of reviving data/pdf.
        destination_dir = self.settings.paths.common_pdf_dir / "full-text" / candidate.source
        destination_dir.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(candidate.doi.encode("utf-8")).hexdigest()[:24]
        destination = destination_dir / f"{stem}.pdf"
        if destination.is_file() and destination.stat().st_size > 0:
            return DownloadedFullText(
                path=destination.resolve(),
                final_url=candidate.url,
                sha256=sha256_file(destination),
                byte_count=destination.stat().st_size,
                media_type="application/pdf",
            )

        headers = {"Accept": "application/pdf", "User-Agent": "CiderScholar/2.0"}
        if candidate.source == "istex":
            token = os.environ.get(self.config.istex_token_env, "").strip()
            if not token:
                raise PermissionError(f"ISTEX requires {self.config.istex_token_env}")
            headers["Authorization"] = f"Bearer {token}"

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stem}.", suffix=".tmp", dir=destination_dir
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            final_url, media_type, byte_count = self._stream(
                candidate.source,
                candidate.url,
                temporary,
                headers,
            )
            with temporary.open("rb") as handle:
                signature = handle.read(5)
            if signature != b"%PDF-":
                raise ValueError("provider response is not a PDF")
            temporary.replace(destination)
            return DownloadedFullText(
                path=destination.resolve(),
                final_url=final_url,
                sha256=sha256_file(destination),
                byte_count=byte_count,
                media_type=media_type,
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def download_native(self, candidate: NativeFullTextCandidate) -> DownloadedFullText:
        """Persist an authenticated JATS/TEI/text body without feeding it to the PDF RAG yet."""

        destination_dir = self.settings.paths.common_full_text_assets_dir / candidate.source
        destination_dir.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(candidate.doi.encode("utf-8")).hexdigest()[:24]
        destination = destination_dir / f"{stem}{_native_asset_suffix(candidate.format)}"
        if destination.is_file() and destination.stat().st_size > 0:
            return DownloadedFullText(
                path=destination.resolve(),
                final_url=candidate.url,
                sha256=sha256_file(destination),
                byte_count=destination.stat().st_size,
                media_type=candidate.media_type,
            )

        headers = {"Accept": candidate.media_type, "User-Agent": "CiderScholar/2.0"}
        if candidate.source == "istex":
            token = os.environ.get(self.config.istex_token_env, "").strip()
            if not token:
                raise PermissionError(f"ISTEX requires {self.config.istex_token_env}")
            headers["Authorization"] = f"Bearer {token}"

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{stem}.", suffix=".tmp", dir=destination_dir
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            final_url, media_type, byte_count = self._stream(
                candidate.source,
                candidate.url,
                temporary,
                headers,
            )
            _validate_native_asset(temporary, candidate.format)
            temporary.replace(destination)
            return DownloadedFullText(
                path=destination.resolve(),
                final_url=final_url,
                sha256=sha256_file(destination),
                byte_count=byte_count,
                media_type=media_type,
            )
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _stream(
        self,
        source: str,
        initial_url: str,
        destination: Path,
        headers: Mapping[str, str],
    ) -> tuple[str, str, int]:
        current = initial_url
        with httpx.Client(timeout=self.config.timeout_seconds, trust_env=False) as client:
            for _redirect in range(6):
                _validate_public_https_url(current)
                with client.stream(
                    "GET", current, headers=headers, follow_redirects=False
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("full-text redirect has no destination")
                        current = urljoin(current, location)
                        continue
                    if response.status_code == 429 or (
                        response.status_code == 503 and response.headers.get("Retry-After")
                    ):
                        retry_at = _retry_at_from_headers(
                            response.headers,
                            default_hours=self.config.default_rate_limit_cooldown_hours,
                        )
                        raise ProviderDeferred(
                            f"{source} download returned HTTP {response.status_code}; "
                            f"retry after {retry_at.isoformat()}",
                            retry_at=retry_at,
                            status_code=response.status_code,
                        )
                    if response.is_error:
                        raise FullTextApiError(
                            f"full-text download returned HTTP {response.status_code}"
                        )
                    expected = response.headers.get("Content-Length")
                    if expected and int(expected) > self.config.max_download_bytes:
                        raise ValueError("full-text PDF exceeds the configured size limit")
                    byte_count = 0
                    with destination.open("wb") as handle:
                        for block in response.iter_bytes(1024 * 1024):
                            byte_count += len(block)
                            if byte_count > self.config.max_download_bytes:
                                raise ValueError("full-text PDF exceeds the configured size limit")
                            handle.write(block)
                    media_type = response.headers.get("Content-Type", "application/pdf").split(
                        ";", 1
                    )[0]
                    return str(response.url), media_type, byte_count
        raise ValueError("full-text download exceeded the redirect limit")


def _native_asset_suffix(format_name: NativeFullTextFormat) -> str:
    return {
        "jats_xml": ".jats.xml",
        "tei_xml": ".tei.xml",
        "structured_xml": ".xml",
        "cleaned_text": ".txt",
        "plain_text": ".txt",
    }[format_name]


def _validate_native_asset(path: Path, format_name: NativeFullTextFormat) -> None:
    """Reject HTML/login pages and obviously invalid payloads before committing an asset."""

    with path.open("rb") as handle:
        prefix = handle.read(4096).lstrip().lower()
    if not prefix:
        raise ValueError("provider returned an empty native full-text asset")
    if prefix.startswith((b"<!doctype html", b"<html", b"<head")):
        raise ValueError("provider response is an HTML page, not native full text")
    if format_name in {"jats_xml", "tei_xml", "structured_xml"} and not prefix.startswith(b"<"):
        raise ValueError("provider response is not an XML full-text asset")
    if format_name in {"cleaned_text", "plain_text"} and b"\x00" in prefix:
        raise ValueError("provider response is not a text full-text asset")


class FullTextHarvestService:
    """Audit every DOI and ingest only theme-accepted, accessible PDF full texts."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        *,
        rag_settings: Settings | None = None,
        rag_database: Database | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.rag_settings = rag_settings or settings
        self.rag_database = rag_database or database
        self.store = FullTextStore(database)

    def run(
        self,
        *,
        audit_only: bool = False,
        include_slow_fallbacks: bool = True,
        max_downloads: int | None = None,
        max_native_downloads: int | None = None,
        record_ids: Sequence[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[FullTextAuditReport, FullTextHarvestReport]:
        records = self.store.doi_records()
        if record_ids is not None:
            selected_ids = set(dict.fromkeys(record_ids))
            records = [record for record in records if str(record["id"]) in selected_ids]
        cached = self.store.cached_observations(
            max_age_hours=self.settings.full_text.availability_cache_hours,
            sources=self.settings.full_text.sources,
        )
        with FullTextAuditService(self.settings, store=self.store) as auditor:
            audit = auditor.audit(
                records,
                progress=progress,
                include_slow_fallbacks=include_slow_fallbacks,
                seed_observations=cached,
                observation_callback=self.store.upsert_observation,
                observation_batch_callback=self.store.upsert_observations,
            )
        records_by_id = {str(record["id"]): record for record in records}
        persisted_observations: list[tuple[Mapping[str, Any], FullTextObservation]] = []
        for audited in audit.records:
            record = records_by_id[audited.record_id]
            for observation in audited.observations:
                persisted_observations.append((record, observation))
        self.store.upsert_observations(persisted_observations)

        native_candidates = _selected_native_candidates(audit.records)
        failed_candidate_keys = self.store.failed_candidate_keys()
        failed_native_candidate_keys = self.store.failed_native_candidate_keys()
        downloaded_native_candidate_keys = self.store.downloaded_native_candidate_keys()
        all_candidates = [
            record
            for record in audit.records
            if record.relevance_status == "accepted" and record.selected is not None
        ]
        candidates = [
            record
            for record in all_candidates
            if record.selected is not None
            and (record.record_id, record.selected.source) not in failed_candidate_keys
        ]
        candidates.sort(
            key=lambda record: (
                _candidate_priority(record.selected.source if record.selected else ""),
                record.doi,
            )
        )
        limit = max_downloads or self.configured_download_limit
        counters = {
            "already_ingested": 0,
            "downloaded": 0,
            "ingested": 0,
            "duplicate": 0,
            "ocr_required": 0,
            "deferred": 0,
            "failed": 0,
            "native_downloaded": 0,
            "native_already_downloaded": 0,
            "native_deferred": 0,
            "native_failed": 0,
        }
        article_ids: list[str] = []
        errors: list[dict[str, str]] = []
        blocked_hosts: dict[str, int] = {}
        if not audit_only:
            downloader = FullTextDownloader(self.rag_settings)
            native_limit = max_native_downloads or self.configured_native_download_limit
            native_download_attempts = 0
            for audited, candidate in native_candidates:
                native_key = (audited.record_id, candidate.source, candidate.format)
                if native_key in downloaded_native_candidate_keys:
                    counters["native_already_downloaded"] += 1
                    continue
                if native_key in failed_native_candidate_keys:
                    continue
                hostname = (urlsplit(candidate.url).hostname or "").casefold()
                cooldown = self.store.active_cooldown(candidate.source, host=hostname)
                if cooldown is not None:
                    counters["native_deferred"] += 1
                    errors.append(
                        {
                            "doi": audited.doi,
                            "source": candidate.source,
                            "format": candidate.format,
                            "error_type": "ProviderDeferred",
                            "message": (
                                f"nouvelle tentative interdite avant {cooldown['retry_at']}"
                            ),
                        }
                    )
                    continue
                if blocked_hosts.get(hostname, 0) >= 3:
                    error = PermissionError(
                        "hÃ´te arrÃªtÃ© aprÃ¨s trois refus HTTP 403 consÃ©cutifs"
                    )
                    counters["native_failed"] += 1
                    errors.append(
                        {
                            "doi": audited.doi,
                            "source": candidate.source,
                            "format": candidate.format,
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    )
                    self.store.update_native_asset(
                        record_id=audited.record_id,
                        candidate=candidate,
                        state="failed",
                        error=error,
                    )
                    continue
                if native_download_attempts >= native_limit:
                    break
                native_download_attempts += 1
                if progress:
                    progress(
                        "full text natif "
                        f"{native_download_attempts}/{native_limit}: "
                        f"{candidate.source}/{candidate.format} {audited.doi}"
                    )
                try:
                    self.store.update_native_asset(
                        record_id=audited.record_id,
                        candidate=candidate,
                        state="downloading",
                    )
                    downloaded = downloader.download_native(candidate)
                    counters["native_downloaded"] += 1
                    self.store.update_native_asset(
                        record_id=audited.record_id,
                        candidate=candidate,
                        state="downloaded",
                        downloaded=downloaded,
                    )
                except Exception as exc:
                    if isinstance(exc, ProviderDeferred):
                        self.store.set_cooldown(
                            candidate.source,
                            host=hostname,
                            retry_at=exc.retry_at,
                            reason=str(exc),
                            status_code=exc.status_code,
                        )
                        counters["native_deferred"] += 1
                        errors.append(
                            {
                                "doi": audited.doi,
                                "source": candidate.source,
                                "format": candidate.format,
                                "error_type": type(exc).__name__,
                                "message": str(exc)[:300],
                            }
                        )
                        self.store.update_native_asset(
                            record_id=audited.record_id,
                            candidate=candidate,
                            state="available",
                            error=exc,
                        )
                        continue
                    if isinstance(exc, httpx.TimeoutException):
                        self.store.set_cooldown(
                            candidate.source,
                            host=hostname,
                            retry_at=datetime.now(UTC)
                            + timedelta(hours=self.settings.full_text.timeout_cooldown_hours),
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    if isinstance(exc, FullTextApiError) and "HTTP 403" in str(exc):
                        blocked_hosts[hostname] = blocked_hosts.get(hostname, 0) + 1
                        if blocked_hosts[hostname] >= 3:
                            self.store.set_cooldown(
                                candidate.source,
                                host=hostname,
                                retry_at=datetime.now(UTC)
                                + timedelta(
                                    hours=self.settings.full_text.protected_host_cooldown_hours
                                ),
                                reason="trois refus HTTP 403 consÃ©cutifs; protection respectÃ©e",
                                status_code=403,
                            )
                    else:
                        blocked_hosts[hostname] = 0
                    counters["native_failed"] += 1
                    errors.append(
                        {
                            "doi": audited.doi,
                            "source": candidate.source,
                            "format": candidate.format,
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:300],
                        }
                    )
                    self.store.update_native_asset(
                        record_id=audited.record_id,
                        candidate=candidate,
                        state="failed",
                        error=exc,
                    )
            pipeline = IngestionPipeline(self.rag_settings, self.rag_database)
            download_attempts = 0
            for audited in candidates:
                assert audited.selected is not None
                candidate = audited.selected
                existing = self.rag_database.article_by_doi(audited.doi)
                if existing is not None and self.rag_database.chunk_count(str(existing["id"])) > 0:
                    counters["already_ingested"] += 1
                    article_ids.append(str(existing["id"]))
                    self.store.update_asset(
                        record_id=audited.record_id,
                        source=candidate.source,
                        state="ingested",
                        article_id=self._asset_article_id(str(existing["id"])),
                    )
                    continue
                hostname = (urlsplit(candidate.url).hostname or "").casefold()
                cooldown = self.store.active_cooldown(candidate.source, host=hostname)
                if cooldown is not None:
                    counters["deferred"] += 1
                    errors.append(
                        {
                            "doi": audited.doi,
                            "source": candidate.source,
                            "error_type": "ProviderDeferred",
                            "message": (
                                f"nouvelle tentative interdite avant {cooldown['retry_at']}"
                            ),
                        }
                    )
                    continue
                if blocked_hosts.get(hostname, 0) >= 3:
                    error = PermissionError("hôte arrêté après trois refus HTTP 403 consécutifs")
                    counters["failed"] += 1
                    errors.append(
                        {
                            "doi": audited.doi,
                            "source": candidate.source,
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    )
                    self.store.update_asset(
                        record_id=audited.record_id,
                        source=candidate.source,
                        state="failed",
                        error=error,
                    )
                    continue
                if download_attempts >= limit:
                    break
                download_attempts += 1
                if progress:
                    progress(
                        f"full text {download_attempts}/{limit}: {candidate.source} {audited.doi}"
                    )
                try:
                    self.store.update_asset(
                        record_id=audited.record_id,
                        source=candidate.source,
                        state="downloading",
                    )
                    downloaded = downloader.download(candidate)
                    counters["downloaded"] += 1
                    self.store.update_asset(
                        record_id=audited.record_id,
                        source=candidate.source,
                        state="downloaded",
                        downloaded=downloaded,
                    )
                    metadata = _catalog_metadata(records_by_id[audited.record_id], candidate.source)
                    ingestion = pipeline.ingest_file(
                        downloaded.path,
                        catalog_metadata=metadata,
                    )
                    if ingestion.status in {"chunks_ready", "duplicate"} and ingestion.article_id:
                        counter = "ingested" if ingestion.status == "chunks_ready" else "duplicate"
                        counters[counter] += 1
                        article_ids.append(ingestion.article_id)
                        self.store.update_asset(
                            record_id=audited.record_id,
                            source=candidate.source,
                            state="ingested",
                            article_id=self._asset_article_id(ingestion.article_id),
                            downloaded=downloaded,
                        )
                    elif ingestion.status == "ocr_required":
                        counters["ocr_required"] += 1
                        error = RuntimeError("downloaded PDF requires OCR")
                        self.store.update_asset(
                            record_id=audited.record_id,
                            source=candidate.source,
                            state="failed",
                            downloaded=downloaded,
                            error=error,
                        )
                    else:
                        raise RuntimeError(ingestion.error_message or "PDF ingestion failed")
                except Exception as exc:
                    if isinstance(exc, ProviderDeferred):
                        self.store.set_cooldown(
                            candidate.source,
                            host=hostname,
                            retry_at=exc.retry_at,
                            reason=str(exc),
                            status_code=exc.status_code,
                        )
                        counters["deferred"] += 1
                        errors.append(
                            {
                                "doi": audited.doi,
                                "source": candidate.source,
                                "error_type": type(exc).__name__,
                                "message": str(exc)[:300],
                            }
                        )
                        self.store.update_asset(
                            record_id=audited.record_id,
                            source=candidate.source,
                            state="available",
                            error=exc,
                        )
                        continue
                    if isinstance(exc, httpx.TimeoutException):
                        self.store.set_cooldown(
                            candidate.source,
                            host=hostname,
                            retry_at=datetime.now(UTC)
                            + timedelta(hours=self.settings.full_text.timeout_cooldown_hours),
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    if isinstance(exc, FullTextApiError) and "HTTP 403" in str(exc):
                        blocked_hosts[hostname] = blocked_hosts.get(hostname, 0) + 1
                        if blocked_hosts[hostname] >= 3:
                            self.store.set_cooldown(
                                candidate.source,
                                host=hostname,
                                retry_at=datetime.now(UTC)
                                + timedelta(
                                    hours=(self.settings.full_text.protected_host_cooldown_hours)
                                ),
                                reason="trois refus HTTP 403 consécutifs; protection respectée",
                                status_code=403,
                            )
                    else:
                        blocked_hosts[hostname] = 0
                    counters["failed"] += 1
                    errors.append(
                        {
                            "doi": audited.doi,
                            "source": candidate.source,
                            "error_type": type(exc).__name__,
                            "message": str(exc)[:300],
                        }
                    )
                    self.store.update_asset(
                        record_id=audited.record_id,
                        source=candidate.source,
                        state="failed",
                        error=exc,
                    )
        return audit, FullTextHarvestReport(
            audited_dois=audit.doi_count,
            resolved_dois=audit.resolved_count,
            accepted_candidates=len(all_candidates),
            already_ingested=counters["already_ingested"],
            previously_failed=len(all_candidates) - len(candidates),
            downloaded=counters["downloaded"],
            ingested=counters["ingested"],
            duplicate=counters["duplicate"],
            ocr_required=counters["ocr_required"],
            deferred=counters["deferred"],
            failed=counters["failed"],
            article_ids=list(dict.fromkeys(article_ids)),
            errors=errors,
            native_downloaded=counters["native_downloaded"],
            native_already_downloaded=counters["native_already_downloaded"],
            native_deferred=counters["native_deferred"],
            native_failed=counters["native_failed"],
        )

    @property
    def configured_download_limit(self) -> int:
        return self.settings.full_text.max_downloads_per_run

    @property
    def configured_native_download_limit(self) -> int:
        return self.settings.full_text.max_native_downloads_per_run

    def _asset_article_id(self, article_id: str) -> str | None:
        if self.database.path.resolve() == self.rag_database.path.resolve():
            return article_id
        return None


def _europe_pmc_candidate(doi: str, hit: Mapping[str, Any]) -> FullTextCandidate | None:
    if str(hit.get("isOpenAccess") or "").upper() != "Y":
        return None
    raw_urls = hit.get("fullTextUrlList")
    urls = raw_urls.get("fullTextUrl") if isinstance(raw_urls, dict) else None
    for value in urls if isinstance(urls, list) else []:
        if not isinstance(value, dict):
            continue
        if (
            value.get("site") == "Europe_PMC"
            and value.get("documentStyle") == "pdf"
            and value.get("availabilityCode") == "OA"
            and isinstance(value.get("url"), str)
        ):
            return FullTextCandidate(
                doi=doi,
                source="europe_pmc",
                provider_id=(str(hit.get("pmcid")) if hit.get("pmcid") else None),
                url=str(value["url"]),
                media_type="application/pdf",
                license=_first_license(hit),
            )
    return None


def _europe_pmc_native_candidates(
    doi: str,
    hit: Mapping[str, Any],
) -> list[NativeFullTextCandidate]:
    """Return JATS XML only for Europe PMC records explicitly marked open access."""

    pmcid = str(hit.get("pmcid") or "").strip()
    if str(hit.get("isOpenAccess") or "").upper() != "Y" or not pmcid:
        return []
    return [
        NativeFullTextCandidate(
            doi=doi,
            source="europe_pmc",
            format="jats_xml",
            provider_id=pmcid,
            url=f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
            media_type="application/xml",
            license=_first_license(hit),
        )
    ]


def _istex_native_candidates(
    doi: str,
    provider_id: str,
    assets: Sequence[Any],
    *,
    base_url: str,
    requires_authentication: bool,
) -> list[NativeFullTextCandidate]:
    """Select the most structured ISTEX body format without treating it as a PDF."""

    formats = {
        "tei": ("tei_xml", "application/tei+xml"),
        "cleaned": ("cleaned_text", "text/plain"),
    }
    candidates: list[NativeFullTextCandidate] = []
    seen_formats: set[str] = set()
    for asset in assets:
        if not isinstance(asset, Mapping):
            continue
        extension = str(asset.get("extension") or "").strip().casefold()
        selected = formats.get(extension)
        if selected is None:
            continue
        format_name, media_type = selected
        if format_name in seen_formats:
            continue
        seen_formats.add(format_name)
        candidates.append(
            NativeFullTextCandidate(
                doi=doi,
                source="istex",
                format=format_name,
                provider_id=provider_id,
                url=f"{base_url.rstrip('/')}/document/{provider_id}/fulltext/{extension}",
                media_type=media_type,
                requires_authentication=requires_authentication,
            )
        )
    return candidates


def _native_candidates_from_explicit_links(
    doi: str | None,
    *,
    source: str,
    provider_id: str | None,
    links: Sequence[Any],
    url_key: str,
    media_type_key: str,
    require_fulltext_type: bool = False,
) -> list[NativeFullTextCandidate]:
    """Map only provider-declared XML/text full-text links to a native artifact.

    The resolver never guesses a format from an extension or a landing page. This makes the
    adapter safe for any source exposing a typed full-text link (currently DOAJ and Crossref).
    """

    if doi is None:
        return []
    formats = {
        "application/jats+xml": "jats_xml",
        "application/tei+xml": "tei_xml",
        "application/xml": "structured_xml",
        "text/xml": "structured_xml",
        "text/plain": "plain_text",
    }
    candidates: list[NativeFullTextCandidate] = []
    seen_formats: set[str] = set()
    for link in links:
        if not isinstance(link, Mapping):
            continue
        if require_fulltext_type and str(link.get("type") or "").casefold() != "fulltext":
            continue
        media_type = str(link.get(media_type_key) or "").split(";", 1)[0].strip().casefold()
        format_name = formats.get(media_type)
        url = link.get(url_key)
        if format_name is None or format_name in seen_formats:
            continue
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        seen_formats.add(format_name)
        candidates.append(
            NativeFullTextCandidate(
                doi=doi,
                source=source,
                format=format_name,
                provider_id=provider_id,
                url=url,
                media_type=media_type,
            )
        )
    return candidates


def _first_license(hit: Mapping[str, Any]) -> str | None:
    licenses = hit.get("license")
    if isinstance(licenses, str) and licenses.strip():
        return licenses.strip()
    return None


def _openalex_candidate(doi: str, hit: Mapping[str, Any]) -> FullTextCandidate | None:
    locations: list[Any] = [hit.get("best_oa_location"), hit.get("primary_location")]
    raw_locations = hit.get("locations")
    if isinstance(raw_locations, list):
        locations.extend(raw_locations)
    for location in locations:
        if not isinstance(location, dict):
            continue
        url = location.get("pdf_url")
        if isinstance(url, str) and url.startswith("https://"):
            provider_id = str(hit.get("id") or "").rsplit("/", 1)[-1] or None
            return FullTextCandidate(
                doi=doi,
                source="openalex",
                provider_id=provider_id,
                url=url,
                media_type="application/pdf",
                license=(str(location.get("license")) if location.get("license") else None),
            )
    return None


def _select_candidate(
    observations: Iterable[FullTextObservation],
) -> FullTextCandidate | None:
    candidates = [
        observation.candidate
        for observation in observations
        if observation.state == "available" and observation.candidate is not None
    ]
    return min(
        candidates,
        key=lambda candidate: _candidate_priority(candidate.source),
        default=None,
    )


def _candidate_priority(source: str) -> int:
    return {
        "europe_pmc": 0,
        "istex": 1,
        "core": 2,
        "hal": 3,
        "semantic_scholar": 4,
        "openalex": 5,
        "unpaywall": 6,
        "doaj": 7,
        "crossref": 8,
        "elsevier": 9,
    }.get(source, 99)


def _native_candidate_priority(candidate: NativeFullTextCandidate) -> tuple[int, int, str]:
    format_priority = {
        "jats_xml": 0,
        "tei_xml": 1,
        "structured_xml": 2,
        "cleaned_text": 3,
        "plain_text": 4,
    }
    return (
        _candidate_priority(candidate.source),
        format_priority[candidate.format],
        candidate.url,
    )


def _selected_native_candidates(
    records: Sequence[FullTextAuditRecord],
) -> list[tuple[FullTextAuditRecord, NativeFullTextCandidate]]:
    """Keep one best native body per accepted article to avoid redundant provider downloads."""

    selected: list[tuple[FullTextAuditRecord, NativeFullTextCandidate]] = []
    for record in records:
        if record.relevance_status != "accepted":
            continue
        candidates = [
            candidate
            for observation in record.observations
            if observation.state == "available"
            for candidate in observation.native_candidates
            if not candidate.requires_authentication
        ]
        candidate = min(candidates, key=_native_candidate_priority, default=None)
        if candidate is not None:
            selected.append((record, candidate))
    return sorted(selected, key=lambda value: (_native_candidate_priority(value[1]), value[0].doi))


def _batches(values: Sequence[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _parse_sqlite_utc(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=UTC)


def _retry_at_from_headers(
    headers: Mapping[str, str],
    *,
    default_hours: int,
) -> datetime:
    now = datetime.now(UTC)
    raw = str(headers.get("Retry-After") or "").strip()
    if raw:
        try:
            seconds = max(0, int(raw))
            return now + timedelta(seconds=seconds)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max(now, parsed.astimezone(UTC))
            except (TypeError, ValueError, OverflowError):
                pass
    for header in ("RateLimit-Reset", "X-RateLimit-Reset"):
        raw_reset = str(headers.get(header) or "").strip()
        if not raw_reset:
            continue
        try:
            reset_value = float(raw_reset)
        except ValueError:
            continue
        if reset_value > now.timestamp() + 60:
            return datetime.fromtimestamp(reset_value, tz=UTC)
        return now + timedelta(seconds=max(0.0, reset_value))
    return now + timedelta(hours=default_hours)


def _validate_public_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise UnsafeFullTextUrl("full-text URL must be public HTTPS without credentials")
    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeFullTextUrl("full-text URL cannot target localhost")
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise UnsafeFullTextUrl("full-text hostname could not be resolved") from exc
    for address in addresses:
        parsed_address = ipaddress.ip_address(address)
        if not parsed_address.is_global:
            raise UnsafeFullTextUrl("full-text URL resolved to a non-public address")


def _catalog_metadata(record: Mapping[str, Any], source: str) -> PdfCatalogMetadata:
    try:
        raw_authors = json.loads(str(record.get("authors") or "[]"))
    except json.JSONDecodeError:
        raw_authors = []
    authors = [str(author) for author in raw_authors] if isinstance(raw_authors, list) else []
    return PdfCatalogMetadata(
        title=str(record["title"]),
        doi=str(record["doi"]),
        abstract=(str(record["abstract"]) if record.get("abstract") else None),
        authors=authors,
        journal=(str(record["journal"]) if record.get("journal") else None),
        work_type=(str(record["work_type"]) if record.get("work_type") else None),
        publisher=(str(record["publisher"]) if record.get("publisher") else None),
        publication_year=(
            int(record["publication_year"]) if record.get("publication_year") else None
        ),
        source=f"Full text API: {source}",
    )
