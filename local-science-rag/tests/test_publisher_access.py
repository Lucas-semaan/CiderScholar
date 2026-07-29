from __future__ import annotations

import sqlite3
from contextlib import closing

import httpx
from fastapi.testclient import TestClient

from app.config import PublisherProfile
from app.database.sqlite import Database
from app.main import create_app
from app.publisher_access.credentials import PublisherCredentialStore
from app.publisher_access.downloader import AuthorizedCookieDownloader
from app.publisher_access.service import PublisherCollectionService


def _profile() -> PublisherProfile:
    return PublisherProfile(
        id="publisher_test",
        label="Publisher test",
        login_url="https://auth.publisher.example/login",
        allowed_domains=["publisher.example"],
        username_selector="#username",
        password_selector="#password",
        submit_selector="button[type=submit]",
        success_selector="#authenticated",
        pdf_link_selectors=["a.pdf"],
    )


def _enable(settings) -> None:
    settings.app.offline_mode = False
    settings.app.allow_publisher_automation = True
    settings.publisher_access.enabled = True
    settings.publisher_access.profiles = [_profile()]


def _insert_record(database: Database) -> None:
    with closing(database.connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, journal,
                publication_year, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "record-1",
                "doi:10.1000/test",
                "10.1000/test",
                "Authorized publisher test",
                "A test abstract.",
                "[]",
                "Test Journal",
                2026,
                "a" * 64,
            ),
        )


def test_cookie_bridge_reuses_authorized_browser_cookie() -> None:
    seen_cookie = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_cookie
        seen_cookie = request.headers.get("cookie", "")
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7 authorized",
        )

    with AuthorizedCookieDownloader(
        browser_cookies=[
            {
                "name": "session",
                "value": "authorized",
                "domain": ".publisher.example",
                "path": "/",
            }
        ],
        allowed_domains=["publisher.example"],
        timeout_seconds=10,
        max_bytes=1024,
        referer="https://publisher.example/article",
        transport=httpx.MockTransport(handler),
    ) as downloader:
        document = downloader.download("https://publisher.example/article.pdf")

    assert document.content.startswith(b"%PDF")
    assert seen_cookie == "session=authorized"


def test_cookie_bridge_rejects_redirect_outside_allow_list() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://untrusted.example/file.pdf"})

    with AuthorizedCookieDownloader(
        browser_cookies=[],
        allowed_domains=["publisher.example"],
        timeout_seconds=10,
        max_bytes=1024,
        referer="https://publisher.example/article",
        transport=httpx.MockTransport(handler),
    ) as downloader:
        try:
            downloader.download("https://publisher.example/article.pdf")
        except ValueError as exc:
            assert "allow-list" in str(exc)
        else:
            raise AssertionError("cross-domain publisher redirect must be rejected")


def test_publisher_assets_are_linked_to_bibliographic_records(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_record(database)
    records, missing = database.publisher_records_for_targets(["10.1000/TEST"])
    assert not missing
    run_id = database.create_publisher_access_run(
        profile_id="publisher_test",
        authorization_reference="authorization-ticket-1",
        record_ids=["record-1"],
    )
    database.start_publisher_access_run(run_id)
    database.mark_publisher_item_processing(run_id, "record-1")
    asset_id = database.save_publisher_asset(
        run_id=run_id,
        record_id="record-1",
        article_id=None,
        profile_id="publisher_test",
        acquisition_method="browser_pdf_link",
        source_url="https://doi.org/10.1000/test",
        final_url="https://publisher.example/article.pdf",
        media_type="application/pdf",
        file_path="data/pdf/publisher/test.pdf",
        sha256="b" * 64,
        byte_count=200,
    )
    database.complete_publisher_access_run(run_id)

    run = database.publisher_access_run(run_id)
    assert run is not None
    assert run["state"] == "completed"
    assert run["items"][0]["asset_id"] == asset_id
    assert records[0]["id"] == "record-1"


def test_publisher_api_never_returns_ldap_password(settings, monkeypatch) -> None:
    _enable(settings)
    captured: dict[str, str] = {}

    def save(_self, *, username: str, password: str) -> None:
        captured.update(username=username, password=password)

    monkeypatch.setattr(PublisherCredentialStore, "save", save)
    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/api/publisher-access/credentials",
            json={
                "username": "ldap-user",
                "password": "ldap-secret",
                "authorization_confirmed": True,
            },
        )

    assert response.status_code == 200
    assert captured == {"username": "ldap-user", "password": "ldap-secret"}
    assert "ldap-secret" not in response.text


def test_authorized_collection_endpoint_is_explicit_and_bounded(settings, monkeypatch) -> None:
    _enable(settings)
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_record(database)
    monkeypatch.setattr(PublisherCredentialStore, "configured", lambda _self: True)
    monkeypatch.setattr(PublisherCollectionService, "run", lambda _self, **_kwargs: None)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/publisher-access/runs",
            json={
                "profile_id": "publisher_test",
                "targets": ["10.1000/test"],
                "authorization_reference": "authorization-ticket-1",
                "authorization_confirmed": True,
            },
        )

    assert response.status_code == 202
    assert response.json()["target_count"] == 1


def test_publisher_tables_reject_assets_for_unknown_records(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with closing(database.connect()) as connection:
        try:
            connection.execute(
                """
                INSERT INTO publisher_full_text_assets (
                    id, record_id, run_id, profile_id, acquisition_method,
                    source_url, final_url, media_type, file_path, sha256, byte_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "asset",
                    "missing-record",
                    "missing-run",
                    "publisher_test",
                    "browser_pdf_link",
                    "https://doi.org/10.1000/test",
                    "https://publisher.example/test.pdf",
                    "application/pdf",
                    "test.pdf",
                    "c" * 64,
                    100,
                ),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("publisher assets must retain bibliographic provenance")
