from __future__ import annotations

import json

import httpx

from app.config import FullTextConfig
from app.database.migrations import CURRENT_SCHEMA_VERSION
from app.database.sqlite import Database
from app.updates.full_text import (
    FullTextAuditReport,
    FullTextAuditService,
    FullTextCandidate,
    FullTextDownloader,
    FullTextHarvestService,
    FullTextObservation,
    FullTextStore,
)


def _record(identifier: str, doi: str, status: str = "accepted") -> dict[str, object]:
    return {
        "id": identifier,
        "doi": doi,
        "title": f"Title {identifier}",
        "relevance_status": status,
        "relevance_theme": "microbiologie",
    }


def test_europe_pmc_pdf_is_selected_before_authenticated_istex(settings, monkeypatch) -> None:
    monkeypatch.delenv("ISTEX_API_TOKEN", raising=False)
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["europe_pmc", "istex"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.ebi.ac.uk":
            payload = {
                "resultList": {
                    "result": [
                        {
                            "doi": "10.1371/example",
                            "pmcid": "PMC123",
                            "isOpenAccess": "Y",
                            "fullTextUrlList": {
                                "fullTextUrl": [
                                    {
                                        "site": "Europe_PMC",
                                        "documentStyle": "pdf",
                                        "availabilityCode": "OA",
                                        "url": "https://europepmc.org/articles/PMC123?pdf=render",
                                    }
                                ]
                            },
                        }
                    ]
                }
            }
            return httpx.Response(200, json=payload)
        payload = {
            "hits": [
                {
                    "id": "ISTEX123",
                    "doi": ["10.1371/example"],
                    "fulltext": [{"extension": "pdf", "mimetype": "application/pdf"}],
                }
            ]
        }
        return httpx.Response(200, json=payload)

    with FullTextAuditService(settings, transport=httpx.MockTransport(handler)) as service:
        report = service.audit(
            [_record("record-1", "10.1371/example")],
            include_slow_fallbacks=False,
        )

    assert report.resolved_count == 1
    assert report.source_available_counts == {"europe_pmc": 1}
    assert report.source_authentication_required_counts == {"istex": 1}
    assert report.records[0].selected is not None
    assert report.records[0].selected.source == "europe_pmc"


def test_istex_is_rechecked_when_token_appears_after_cached_authentication(
    settings,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ISTEX_API_TOKEN", "test-token")
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["istex"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "ISTEX123",
                        "doi": ["10.1371/example"],
                        "fulltext": [{"extension": "pdf", "mimetype": "application/pdf"}],
                    }
                ]
            },
        )

    cached = {
        "10.1371/example": {
            "istex": FullTextObservation(
                source="istex",
                state="authentication_required",
                candidate=FullTextCandidate(
                    doi="10.1371/example",
                    source="istex",
                    provider_id="ISTEX123",
                    url="https://api.istex.fr/document/ISTEX123/fulltext/pdf",
                    requires_authentication=True,
                ),
            )
        }
    }
    with FullTextAuditService(settings, transport=httpx.MockTransport(handler)) as service:
        report = service.audit(
            [_record("record-1", "10.1371/example")],
            include_slow_fallbacks=False,
            seed_observations=cached,
        )

    assert requests == 1
    assert report.source_authentication_required_counts == {}
    assert report.source_available_counts == {"istex": 1}
    assert report.records[0].selected is not None
    assert report.records[0].selected.source == "istex"


def test_istex_download_uses_bearer_token(settings, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ISTEX_API_TOKEN", "test-token")
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["istex"],
                request_delay_seconds=0,
                max_retries=0,
            ),
            "paths": settings.paths.model_copy(update={"pdf_dir": tmp_path / "pdf"}),
        }
    )
    captured_headers: dict[str, str] = {}

    def fake_stream(
        _self,
        _source,
        url,
        destination,
        headers,
    ):
        captured_headers.update(headers)
        destination.write_bytes(b"%PDF-1.7\n%%EOF")
        return url, "application/pdf", destination.stat().st_size

    monkeypatch.setattr(FullTextDownloader, "_stream", fake_stream)
    candidate = FullTextCandidate(
        doi="10.1371/example",
        source="istex",
        provider_id="ISTEX123",
        url="https://api.istex.fr/document/ISTEX123/fulltext/pdf",
    )

    downloaded = FullTextDownloader(settings).download(candidate)

    assert downloaded.path.is_file()
    assert captured_headers["Authorization"] == "Bearer test-token"


def test_full_text_observation_is_persisted_with_schema_migration(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, content_hash,
                embedding_status, relevance_status, relevance_theme
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "record-1",
                "doi:10.1371/example",
                "10.1371/example",
                "Example",
                "Abstract",
                json.dumps(["Author"]),
                "a" * 64,
                "pending",
                "accepted",
                "microbiologie",
            ),
        )

    store = FullTextStore(database)
    record = store.doi_records()[0]
    from app.updates.full_text import FullTextCandidate, FullTextObservation

    store.upsert_observation(
        record,
        FullTextObservation(
            source="europe_pmc",
            state="available",
            candidate=FullTextCandidate(
                doi="10.1371/example",
                source="europe_pmc",
                provider_id="PMC123",
                url="https://europepmc.org/articles/PMC123?pdf=render",
            ),
        ),
    )

    with database.connect() as connection:
        assert connection.execute("SELECT max(version) FROM schema_version").fetchone()[0] == (
            CURRENT_SCHEMA_VERSION
        )
        row = connection.execute(
            "SELECT doi, source, state, provider_id FROM full_text_assets"
        ).fetchone()
    assert tuple(row) == ("10.1371/example", "europe_pmc", "available", "PMC123")


def test_full_text_harvest_can_be_bounded_to_selected_query_records(
    settings,
    monkeypatch,
) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    with database.transaction() as connection:
        for index in (1, 2):
            connection.execute(
                """
                INSERT INTO bibliographic_records (
                    id, canonical_key, doi, title, abstract, authors, content_hash,
                    embedding_status, relevance_status, relevance_theme
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"record-{index}",
                    f"doi:10.1371/example-{index}",
                    f"10.1371/example-{index}",
                    f"Example {index}",
                    "Abstract",
                    json.dumps(["Author"]),
                    str(index) * 64,
                    "pending",
                    "accepted",
                    "microbiologie",
                ),
            )
    captured: list[str] = []

    class FakeAuditService:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def audit(self, records, **_kwargs):
            captured.extend(str(record["id"]) for record in records)
            return FullTextAuditReport(
                doi_count=len(records),
                accepted_doi_count=len(records),
                resolved_count=0,
                resolved_accepted_count=0,
                source_available_counts={},
                source_authentication_required_counts={},
                source_errors={},
                records=[],
            )

    monkeypatch.setattr("app.updates.full_text.FullTextAuditService", FakeAuditService)

    audit, harvest = FullTextHarvestService(settings, database).run(
        audit_only=True,
        record_ids=["record-2"],
    )

    assert captured == ["record-2"]
    assert audit.doi_count == 1
    assert harvest.audited_dois == 1


def test_repository_resolvers_return_only_explicit_pdf_candidates(settings) -> None:
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["core", "hal", "semantic_scholar", "doaj"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.core.ac.uk":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 42,
                            "doi": "10.1371/example",
                            "downloadUrl": "https://core.ac.uk/download/42.pdf",
                        }
                    ]
                },
            )
        if request.url.host == "api.archives-ouvertes.fr":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "docs": [
                            {
                                "docid": "HAL42",
                                "doiId_s": "10.1371/example",
                                "fileMain_s": "https://hal.science/hal-42/document",
                            }
                        ]
                    }
                },
            )
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(
                200,
                json=[
                    {
                        "paperId": "S242",
                        "externalIds": {"DOI": "10.1371/example"},
                        "isOpenAccess": True,
                        "openAccessPdf": {
                            "url": "https://repository.example/article.pdf",
                            "license": "CC-BY",
                        },
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "DOAJ42",
                        "bibjson": {
                            "identifier": [{"type": "doi", "id": "10.1371/example"}],
                            "link": [
                                {
                                    "type": "fulltext",
                                    "content_type": "application/pdf",
                                    "url": "https://journal.example/article.pdf",
                                }
                            ],
                        },
                    }
                ]
            },
        )

    with FullTextAuditService(settings, transport=httpx.MockTransport(handler)) as service:
        report = service.audit(
            [_record("record-1", "10.1371/example")],
            include_slow_fallbacks=False,
        )

    assert report.source_available_counts == {
        "core": 1,
        "hal": 1,
        "semantic_scholar": 1,
        "doaj": 1,
    }
    assert report.records[0].selected is not None
    assert report.records[0].selected.source == "core"


def test_rate_limit_is_persisted_and_blocks_requests_until_retry_time(
    settings,
) -> None:
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["core"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )
    database = Database(settings.paths.database_path)
    database.initialize()
    store = FullTextStore(database)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "3600"})

    for _ in range(2):
        with FullTextAuditService(
            settings,
            store=store,
            transport=httpx.MockTransport(handler),
        ) as service:
            report = service.audit(
                [_record("record-1", "10.1371/example")],
                include_slow_fallbacks=False,
            )
        assert "core" in report.source_errors

    assert calls == 1
    cooldown = store.active_cooldown("core")
    assert cooldown is not None
    assert cooldown["http_status"] == 429
