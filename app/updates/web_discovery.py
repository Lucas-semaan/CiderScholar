"""Auditable, rate-limited discovery from explicitly authorized HTML search engines."""

from __future__ import annotations

import base64
import html
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from app.updates.base import BibliographicApiDeferred
from app.updates.models import normalize_doi

WebSearchEngine = Literal["bing", "duckduckgo", "brave", "yahoo"]


@dataclass(frozen=True)
class WebSearchHit:
    engine: WebSearchEngine
    title: str
    snippet: str | None
    url: str
    doi: str | None


class WebSearchClient:
    """Fetch only result pages; never bypass a block, CAPTCHA, or redirect challenge."""

    def __init__(
        self,
        engine: WebSearchEngine,
        *,
        timeout_seconds: float = 30.0,
        request_delay_seconds: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if engine not in {"bing", "duckduckgo", "brave", "yahoo"}:
            raise ValueError("unsupported HTML search engine")
        self.engine = engine
        self.request_delay_seconds = max(1.0, request_delay_seconds)
        self._last_request_at = 0.0
        self._http = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en,fr;q=0.8",
                "User-Agent": (
                    "Mozilla/5.0 (compatible; CiderScholar/0.1; +local-scientific-corpus)"
                ),
            },
        )

    def search(self, query: str, *, page: int = 0, page_size: int = 20) -> list[WebSearchHit]:
        if not query.strip():
            raise ValueError("web discovery query cannot be empty")
        if not 0 <= page <= 100:
            raise ValueError("web discovery page must be between 0 and 100")
        if not 1 <= page_size <= 50:
            raise ValueError("web discovery page size must be between 1 and 50")
        if self.engine == "bing":
            url = "https://www.bing.com/search"
            params = {
                "q": query,
                "count": page_size,
                "first": page * page_size + 1,
                "setlang": "en-US",
            }
        elif self.engine == "duckduckgo":
            url = "https://html.duckduckgo.com/html/"
            params = {"q": query, "s": page * page_size}
        elif self.engine == "brave":
            url = "https://search.brave.com/search"
            params = {"q": query, "source": "web", "offset": page}
        else:
            url = "https://search.yahoo.com/search"
            params = {"p": query, "b": page * 10 + 1}
        response = self._get(url, params=params)
        raw = response.text
        if self.engine == "duckduckgo" and (
            "anomaly.js" in raw.casefold() or "cc=botnet" in raw.casefold()
        ):
            raise BibliographicApiDeferred(
                "duckduckgo web search returned an anti-automation challenge",
                retry_at=datetime.now(UTC) + timedelta(hours=1),
            )
        if self.engine == "bing":
            parsed = _parse_bing(raw)
        elif self.engine == "duckduckgo":
            parsed = _parse_duckduckgo(raw)
        elif self.engine == "brave":
            parsed = _parse_brave(raw)
        else:
            parsed = _parse_yahoo(raw)
        hits: list[WebSearchHit] = []
        seen: set[tuple[str, str]] = set()
        for title, snippet, candidate_url in parsed:
            resolved_url = _resolve_result_url(candidate_url)
            hostname = (urlsplit(resolved_url).hostname or "").casefold()
            if (
                not title
                or not resolved_url.startswith("https://")
                or hostname
                in {
                    "www.bing.com",
                    "bing.com",
                    "duckduckgo.com",
                    "www.duckduckgo.com",
                    "search.brave.com",
                    "www.search.brave.com",
                    "search.yahoo.com",
                    "www.search.yahoo.com",
                }
            ):
                continue
            key = (title.casefold(), resolved_url.casefold())
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                WebSearchHit(
                    engine=self.engine,
                    title=title,
                    snippet=snippet,
                    url=resolved_url,
                    doi=_extract_doi(title, snippet, resolved_url),
                )
            )
        return hits

    def _get(self, url: str, *, params: dict[str, str | int]) -> httpx.Response:
        remaining = self._last_request_at + self.request_delay_seconds - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()
        try:
            response = self._http.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise BibliographicApiDeferred(
                f"{self.engine} web search timed out",
                retry_at=datetime.now(UTC) + timedelta(hours=6),
            ) from exc
        if response.status_code in {403, 429}:
            raise BibliographicApiDeferred(
                f"{self.engine} web search returned HTTP {response.status_code}",
                retry_at=datetime.now(UTC) + timedelta(hours=1),
                status_code=response.status_code,
            )
        if response.is_redirect:
            raise RuntimeError(f"{self.engine} web search returned a redirect challenge")
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.casefold():
            raise RuntimeError(f"{self.engine} web search returned non-HTML content")
        return response

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> WebSearchClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _SearchHtmlParser(HTMLParser):
    def __init__(self, engine: WebSearchEngine) -> None:
        super().__init__(convert_charrefs=True)
        self.engine = engine
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.results: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.capture: Literal["title", "snippet"] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {key: value or "" for key, value in attrs}
        self.stack.append((tag, normalized))
        classes = set(normalized.get("class", "").split())
        if (
            self.engine == "bing"
            and tag == "li"
            and "b_algo" in classes
            or self.engine == "duckduckgo"
            and tag == "div"
            and "result" in classes
        ):
            self._start_result()
        if self.current is None:
            return
        if tag == "a" and normalized.get("href"):
            if (
                self.engine == "bing"
                and self._inside("h2")
                or self.engine == "duckduckgo"
                and "result__a" in classes
            ):
                self.current["url"] = normalized["href"]
                self.capture = "title"
            elif self.engine == "duckduckgo" and "result__snippet" in classes:
                self.capture = "snippet"
        if (
            self.engine == "bing"
            and tag == "p"
            and self._inside_class("b_caption")
            or self.engine == "duckduckgo"
            and "result__snippet" in classes
        ):
            self.capture = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in {"a", "p"}:
            self.capture = None
        closing_result = False
        for index in range(len(self.stack) - 1, -1, -1):
            stacked_tag, attrs = self.stack[index]
            if stacked_tag != tag:
                continue
            classes = set(attrs.get("class", "").split())
            closing_result = (self.engine == "bing" and tag == "li" and "b_algo" in classes) or (
                self.engine == "duckduckgo" and tag == "div" and "result" in classes
            )
            del self.stack[index:]
            break
        if closing_result:
            self._finish_result()

    def handle_data(self, data: str) -> None:
        if self.current is None or self.capture is None:
            return
        prior = self.current.get(self.capture, "")
        self.current[self.capture] = f"{prior} {data}".strip()

    def close(self) -> None:
        super().close()
        self._finish_result()

    def _start_result(self) -> None:
        self._finish_result()
        self.current = {"title": "", "snippet": "", "url": ""}

    def _finish_result(self) -> None:
        if self.current and self.current.get("title") and self.current.get("url"):
            self.results.append(self.current)
        self.current = None
        self.capture = None

    def _inside(self, tag: str) -> bool:
        return any(stacked_tag == tag for stacked_tag, _ in self.stack)

    def _inside_class(self, name: str) -> bool:
        return any(name in attrs.get("class", "").split() for _, attrs in self.stack)


def _parse_bing(raw: str) -> list[tuple[str, str | None, str]]:
    return _parse(raw, "bing")


def _parse_duckduckgo(raw: str) -> list[tuple[str, str | None, str]]:
    return _parse(raw, "duckduckgo")


def _parse_brave(raw: str) -> list[tuple[str, str | None, str]]:
    parser = _ClassResultParser(
        result_class="snippet",
        result_attribute=("data-type", "web"),
        title_class="search-snippet-title",
        snippet_class="generic-snippet",
    )
    parser.feed(raw)
    parser.close()
    return parser.as_tuples()


def _parse_yahoo(raw: str) -> list[tuple[str, str | None, str]]:
    parser = _ClassResultParser(
        result_class="algo-sr",
        result_attribute=None,
        title_class="title",
        snippet_class="compText",
    )
    parser.feed(raw)
    parser.close()
    return parser.as_tuples()


class _ClassResultParser(HTMLParser):
    """Parse one result container using stable semantic classes, including nested markup."""

    def __init__(
        self,
        *,
        result_class: str,
        result_attribute: tuple[str, str] | None,
        title_class: str,
        snippet_class: str,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.result_class = result_class
        self.result_attribute = result_attribute
        self.title_class = title_class
        self.snippet_class = snippet_class
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.results: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.result_depth: int | None = None
        self.capture: Literal["title", "snippet"] | None = None
        self.capture_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = {key: value or "" for key, value in attrs}
        self.stack.append((tag, normalized))
        classes = set(normalized.get("class", "").split())
        matches_attribute = self.result_attribute is None or (
            normalized.get(self.result_attribute[0]) == self.result_attribute[1]
        )
        if self.result_class in classes and matches_attribute:
            self._finish_result()
            self.current = {"title": "", "snippet": "", "url": ""}
            self.result_depth = len(self.stack)
        if self.current is None:
            return
        if tag == "a" and normalized.get("href") and not self.current["url"]:
            self.current["url"] = normalized["href"]
        if self.title_class in classes:
            if normalized.get("title"):
                self.current["title"] = normalized["title"]
                self.capture = None
                self.capture_depth = None
            else:
                self.capture = "title"
                self.capture_depth = len(self.stack)
        elif self.snippet_class in classes:
            self.capture = "snippet"
            self.capture_depth = len(self.stack)

    def handle_endtag(self, tag: str) -> None:
        closing_depth: int | None = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                closing_depth = index + 1
                del self.stack[index:]
                break
        if closing_depth == self.capture_depth:
            self.capture = None
            self.capture_depth = None
        if closing_depth == self.result_depth:
            self._finish_result()

    def handle_data(self, data: str) -> None:
        if self.current is None or self.capture is None:
            return
        prior = self.current.get(self.capture, "")
        self.current[self.capture] = f"{prior} {data}".strip()

    def close(self) -> None:
        super().close()
        self._finish_result()

    def as_tuples(self) -> list[tuple[str, str | None, str]]:
        return [
            (
                _clean_text(result["title"]),
                _clean_text(result.get("snippet")) or None,
                html.unescape(result["url"]),
            )
            for result in self.results
            if _clean_text(result["title"]) and result.get("url")
        ]

    def _finish_result(self) -> None:
        if self.current and self.current.get("title") and self.current.get("url"):
            self.results.append(self.current)
        self.current = None
        self.result_depth = None
        self.capture = None
        self.capture_depth = None


def _parse(raw: str, engine: WebSearchEngine) -> list[tuple[str, str | None, str]]:
    parser = _SearchHtmlParser(engine)
    parser.feed(raw)
    parser.close()
    return [
        (
            _clean_text(result["title"]),
            _clean_text(result.get("snippet")) or None,
            html.unescape(result["url"]),
        )
        for result in parser.results
        if _clean_text(result["title"])
    ]


def _resolve_result_url(value: str) -> str:
    raw = html.unescape(value.strip())
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parsed = urlsplit(raw)
    query = parse_qs(parsed.query)
    if (parsed.hostname or "").casefold().endswith("duckduckgo.com") and query.get("uddg"):
        return unquote(query["uddg"][0])
    if (parsed.hostname or "").casefold().endswith("bing.com") and query.get("u"):
        encoded = query["u"][0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
            if decoded.startswith("https://"):
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
    if (parsed.hostname or "").casefold().endswith("search.yahoo.com"):
        marker = "/RU="
        if marker in parsed.path:
            encoded = parsed.path.split(marker, 1)[1].split("/RK=", 1)[0]
            decoded = unquote(encoded)
            if decoded.startswith("https://"):
                return decoded
    return unquote(raw)


def _extract_doi(title: str, snippet: str | None, url: str) -> str | None:
    return normalize_doi(" ".join((unquote(url), title, snippet or "")))


def _clean_text(value: object) -> str:
    return " ".join(html.unescape(str(value or "")).split())
