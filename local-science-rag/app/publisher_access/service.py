"""Explicit, auditable publisher browser automation and local PDF ingestion."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from app.config import PublisherProfile, Settings
from app.database.sqlite import Database
from app.ingestion.pipeline import IngestionPipeline, PdfCatalogMetadata
from app.publisher_access.credentials import PublisherCredentialStore
from app.publisher_access.downloader import (
    AuthorizedCookieDownloader,
    domain_is_allowed,
    require_allowed_https,
)

LOGGER = logging.getLogger(__name__)


def profile_by_id(settings: Settings, profile_id: str) -> PublisherProfile | None:
    return next(
        (profile for profile in settings.publisher_access.profiles if profile.id == profile_id),
        None,
    )


def _authors(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return (
        [str(author) for author in parsed if isinstance(author, str)]
        if isinstance(parsed, list)
        else []
    )


def _record_url(record: Any) -> str:
    doi = str(record["doi"] or "").strip()
    if doi:
        return f"https://doi.org/{quote(doi, safe='/()')}"
    url = str(record["url"] or "").strip()
    if not url:
        raise ValueError("bibliographic record has neither DOI nor source URL")
    return url


def _atomic_document_path(*, directory: Path, record_id: str, content: bytes) -> tuple[Path, str]:
    sha256 = hashlib.sha256(content).hexdigest()
    destination = directory / record_id / f"{sha256}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{sha256}.", suffix=".tmp", dir=destination.parent
        )
        try:
            with open(descriptor, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
            Path(temporary_name).replace(destination)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
    return destination, sha256


class PublisherCollectionService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.credentials = PublisherCredentialStore(settings)

    def run(self, *, run_id: str, profile_id: str, records: list[Any]) -> None:
        profile = profile_by_id(self.settings, profile_id)
        if profile is None:
            self.database.fail_publisher_access_run(
                run_id,
                error_type="UnknownPublisherProfile",
                error_message="configured publisher profile no longer exists",
            )
            return
        try:
            self.database.start_publisher_access_run(run_id)
            self._run_browser(run_id=run_id, profile=profile, records=records)
            self.database.complete_publisher_access_run(run_id)
        except Exception as exc:
            LOGGER.exception(
                "Authorized publisher collection failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
            self.database.fail_publisher_access_run(
                run_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    def _run_browser(self, *, run_id: str, profile: PublisherProfile, records: list[Any]) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency contract
            raise RuntimeError(
                "Playwright is unavailable; install the documented Python dependencies"
            ) from exc

        credentials = self.credentials.load()
        with sync_playwright() as playwright:
            channel = (
                None
                if self.settings.publisher_access.browser_channel == "chromium"
                else self.settings.publisher_access.browser_channel
            )
            browser = playwright.chromium.launch(
                channel=channel,
                headless=self.settings.publisher_access.headless,
            )
            try:
                context = browser.new_context(accept_downloads=False)
                page = context.new_page()
                page.set_default_timeout(
                    self.settings.publisher_access.navigation_timeout_seconds * 1000
                )
                self._authenticate(page, profile, credentials.username, credentials.password)
                for index, record in enumerate(records):
                    record_id = str(record["id"])
                    self.database.mark_publisher_item_processing(run_id, record_id)
                    try:
                        self._collect_record(
                            run_id=run_id,
                            profile=profile,
                            record=record,
                            page=page,
                            context=context,
                        )
                    except Exception as exc:
                        self.database.fail_publisher_access_item(
                            run_id=run_id,
                            record_id=record_id,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                    if index + 1 < len(records):
                        time.sleep(self.settings.publisher_access.request_delay_seconds)
            finally:
                browser.close()

    def _authenticate(
        self,
        page: Any,
        profile: PublisherProfile,
        username: str,
        password: str,
    ) -> None:
        page.goto(profile.login_url, wait_until="domcontentloaded")
        page.locator(profile.username_selector).fill(username)
        page.locator(profile.password_selector).fill(password)
        page.locator(profile.submit_selector).click()
        page.locator(profile.success_selector).wait_for(state="visible")

    def _collect_record(
        self,
        *,
        run_id: str,
        profile: PublisherProfile,
        record: Any,
        page: Any,
        context: Any,
    ) -> None:
        source_url = _record_url(record)
        parsed_source = urlsplit(source_url)
        if parsed_source.hostname != "doi.org":
            require_allowed_https(source_url, profile.allowed_domains)
        page.goto(source_url, wait_until="domcontentloaded")
        final_page_url = str(page.url)
        if not domain_is_allowed(urlsplit(final_page_url).hostname, profile.allowed_domains):
            raise ValueError("DOI resolved outside the configured publisher allow-list")
        if profile.article_ready_selector:
            page.locator(profile.article_ready_selector).wait_for(state="visible")

        pdf_url = self._find_pdf_url(page, profile, final_page_url)
        if pdf_url:
            browser_cookies = context.cookies([final_page_url, pdf_url])
            with AuthorizedCookieDownloader(
                browser_cookies=browser_cookies,
                allowed_domains=profile.allowed_domains,
                timeout_seconds=self.settings.publisher_access.navigation_timeout_seconds,
                max_bytes=self.settings.publisher_access.max_download_bytes,
                referer=final_page_url,
            ) as downloader:
                downloaded = downloader.download(pdf_url)
            content = downloaded.content
            final_document_url = downloaded.final_url
            method = "browser_pdf_link"
        else:
            if not profile.full_text_selector:
                raise RuntimeError("publisher page exposed no configured full-text document")
            full_text = page.locator(profile.full_text_selector).inner_text().strip()
            if len(full_text) < 500:
                raise RuntimeError("publisher full-text selector returned insufficient text")
            content = page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
            if len(content) > self.settings.publisher_access.max_download_bytes:
                raise RuntimeError("rendered publisher document exceeds configured byte limit")
            final_document_url = final_page_url
            method = "browser_rendered_pdf"

        record_id = str(record["id"])
        destination, sha256 = _atomic_document_path(
            directory=self.settings.paths.pdf_dir / "publisher",
            record_id=record_id,
            content=content,
        )
        report = IngestionPipeline(self.settings, self.database).ingest_file(
            destination,
            catalog_metadata=PdfCatalogMetadata(
                doi=str(record["doi"]) if record["doi"] else None,
                title=str(record["title"]),
                abstract=str(record["abstract"]) if record["abstract"] else None,
                authors=_authors(record["authors"]),
                journal=str(record["journal"]) if record["journal"] else None,
                publication_year=(
                    int(record["publication_year"]) if record["publication_year"] else None
                ),
                source=f"publisher:{profile.id}",
            ),
        )
        if report.status not in {"chunks_ready", "duplicate"}:
            raise RuntimeError(f"downloaded PDF ingestion ended with {report.status}")
        self.database.save_publisher_asset(
            run_id=run_id,
            record_id=record_id,
            article_id=report.article_id,
            profile_id=profile.id,
            acquisition_method=method,
            source_url=source_url,
            final_url=final_document_url,
            media_type="application/pdf",
            file_path=str(destination),
            sha256=sha256,
            byte_count=len(content),
        )

    @staticmethod
    def _find_pdf_url(page: Any, profile: PublisherProfile, base_url: str) -> str | None:
        for selector in profile.pdf_link_selectors:
            locator = page.locator(selector)
            if locator.count() < 1:
                continue
            href = locator.first.get_attribute("href")
            if href:
                return require_allowed_https(urljoin(base_url, href), profile.allowed_domains)
        return None
