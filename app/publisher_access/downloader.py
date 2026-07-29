"""Transfer an authorized browser session into a bounded HTTP download client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


@dataclass(frozen=True)
class DownloadedDocument:
    content: bytes
    media_type: str
    final_url: str


def domain_is_allowed(hostname: str | None, allowed_domains: list[str]) -> bool:
    if not hostname:
        return False
    normalized = hostname.casefold().rstrip(".")
    return any(
        normalized == domain or normalized.endswith(f".{domain}") for domain in allowed_domains
    )


def require_allowed_https(url: str, allowed_domains: list[str]) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not domain_is_allowed(parsed.hostname, allowed_domains)
    ):
        raise ValueError("publisher URL is outside the authorized HTTPS domain allow-list")
    return url


class AuthorizedCookieDownloader:
    """Reuse Playwright cookies in httpx without exposing them outside this object."""

    def __init__(
        self,
        *,
        browser_cookies: list[dict[str, Any]],
        allowed_domains: list[str],
        timeout_seconds: int,
        max_bytes: int,
        referer: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.allowed_domains = allowed_domains
        self.max_bytes = max_bytes
        cookies = httpx.Cookies()
        for cookie in browser_cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            domain = str(cookie.get("domain") or "").lstrip(".")
            path = str(cookie.get("path") or "/")
            if (
                isinstance(name, str)
                and isinstance(value, str)
                and domain_is_allowed(domain, allowed_domains)
            ):
                cookies.set(name, value, domain=domain, path=path)
        self.client = httpx.Client(
            cookies=cookies,
            follow_redirects=False,
            timeout=timeout_seconds,
            verify=True,
            transport=transport,
            headers={
                "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
                "Referer": referer,
                "User-Agent": "CiderScholar-AuthorizedPublisherTest/0.1 TextDataMining",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> AuthorizedCookieDownloader:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def download(self, url: str) -> DownloadedDocument:
        current = require_allowed_https(url, self.allowed_domains)
        for _redirect in range(6):
            with self.client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RuntimeError("publisher redirect omitted its destination")
                    current = require_allowed_https(
                        urljoin(str(response.url), location), self.allowed_domains
                    )
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                byte_count = 0
                for chunk in response.iter_bytes():
                    byte_count += len(chunk)
                    if byte_count > self.max_bytes:
                        raise RuntimeError("publisher document exceeds configured byte limit")
                    chunks.append(chunk)
                content = b"".join(chunks)
                media_type = response.headers.get("content-type", "application/octet-stream")
                media_type = media_type.split(";", 1)[0].strip().casefold()
                if media_type != "application/pdf" and not content.startswith(b"%PDF"):
                    raise RuntimeError("publisher full-text link did not return a PDF document")
                return DownloadedDocument(
                    content=content,
                    media_type="application/pdf",
                    final_url=str(response.url),
                )
        raise RuntimeError("publisher download exceeded the redirect limit")
