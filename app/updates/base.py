"""Shared bounded HTTP behavior for official bibliographic metadata APIs."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar

import httpx

from app.config import Settings
from app.updates.models import BibliographicRecord


class BibliographicApiError(RuntimeError):
    """An official metadata source could not complete a request."""


class MissingBibliographicCredential(BibliographicApiError):
    """A configured source requires a missing environment variable."""


class BibliographicApiDeferred(BibliographicApiError):
    """A metadata provider has supplied or requires a future retry time."""

    def __init__(
        self,
        message: str,
        *,
        retry_at: datetime,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"{message}; retry_at={retry_at.astimezone(UTC).isoformat()}")
        self.retry_at = retry_at.astimezone(UTC)
        self.status_code = status_code


class OfficialBibliographicClient:
    source_id: ClassVar[str]
    source_label: ClassVar[str]
    api_key_environment_attribute: ClassVar[str | None] = None

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.config = settings.bibliographic
        self._http = httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self._last_request_at = 0.0

    def api_key(self) -> str:
        attribute = self.api_key_environment_attribute
        if attribute is None:
            return ""
        environment_name = str(getattr(self.config, attribute))
        value = os.environ.get(environment_name, "").strip()
        if not value:
            raise MissingBibliographicCredential(
                f"{self.source_label} requires environment variable {environment_name}"
            )
        return value

    def is_available(self) -> bool:
        if self.api_key_environment_attribute is None:
            return True
        environment_name = str(getattr(self.config, self.api_key_environment_attribute))
        return bool(os.environ.get(environment_name, "").strip())

    def _pace(self) -> None:
        remaining = self._last_request_at + self.config.request_delay_seconds - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()

    def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int | float],
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        attempts = self.config.max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._pace()
            try:
                response = self._http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(2**attempt)
                    continue
                break
            if response.is_redirect:
                raise BibliographicApiError(f"{self.source_label} returned a forbidden redirect")
            if response.status_code == 429 or (
                response.status_code == 503 and response.headers.get("Retry-After")
            ):
                retry_at = _retry_at(
                    response.headers,
                    default=datetime.now(UTC) + timedelta(hours=1),
                )
                raise BibliographicApiDeferred(
                    f"{self.source_label} returned HTTP {response.status_code}",
                    retry_at=retry_at,
                    status_code=response.status_code,
                )
            if response.status_code in {500, 502, 503, 504} and attempt < attempts - 1:
                delay = 2**attempt
                time.sleep(max(delay, self.config.request_delay_seconds))
                continue
            if response.is_error:
                raise BibliographicApiError(
                    f"{self.source_label} returned HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise BibliographicApiError(f"{self.source_label} returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise BibliographicApiError(
                    f"{self.source_label} returned an unexpected JSON structure"
                )
            return payload
        if isinstance(last_error, httpx.TimeoutException):
            raise BibliographicApiDeferred(
                f"{self.source_label} timed out after {attempts} attempt(s)",
                retry_at=datetime.now(UTC) + timedelta(hours=6),
            ) from last_error
        raise BibliographicApiError(
            f"{self.source_label} is unavailable after {attempts} attempt(s)"
        ) from last_error

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        raise NotImplementedError

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OfficialBibliographicClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _retry_at(headers: Mapping[str, str], *, default: datetime) -> datetime:
    raw = str(headers.get("Retry-After") or "").strip()
    if raw:
        try:
            return datetime.now(UTC) + timedelta(seconds=max(0, int(raw)))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC)
            except (TypeError, ValueError, OverflowError):
                pass
    for name in ("RateLimit-Reset", "X-RateLimit-Reset"):
        value = str(headers.get(name) or "").strip()
        if not value:
            continue
        try:
            reset = float(value)
        except ValueError:
            continue
        if reset > datetime.now(UTC).timestamp() + 60:
            return datetime.fromtimestamp(reset, tz=UTC)
        return datetime.now(UTC) + timedelta(seconds=max(0.0, reset))
    return default
