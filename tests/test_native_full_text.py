"""Coverage for provider-native full-text assets kept outside the PDF citation path."""

from __future__ import annotations

import json

import httpx

from app.config import FullTextConfig
from app.database.sqlite import Database
from app.updates.full_text import (
    DownloadedFullText,
    FullTextAuditRecord,
    FullTextAuditReport,
    FullTextAuditService,
    FullTextDownloader,
    FullTextHarvestService,
    FullTextObservation,
    FullTextStore,
    NativeFullTextCandidate,
)


def _record(identifier: str, doi: str, status: str = "accepted") -> dict[str, object]:
    return {
        "id": identifier,
        "doi": doi,
        "title": f"Title {identifier}",
        "relevance_status": status,
        "relevance_theme": "microbiologie",
    }


def _insert_record(database: Database, identifier: str, doi: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO bibliographic_records (
                id, canonical_key, doi, title, abstract, authors, content_hash,
                embedding_status, relevance_status, relevance_theme
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                f"doi:{doi}",
                doi,
                f"Title {identifier}",
                "Abstract",
                json.dumps(["Author"]),
                "a" * 64,
                "pending",
                "accepted",
                "microbiologie",
            ),
        )


def test_europe_pmc_jats_is_available_without_a_pdf_candidate(settings) -> None:
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["europe_pmc"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "resultList": {
                    "result": [
                        {
                            "doi": "10.1371/example",
                            "pmcid": "PMC123",
                            "isOpenAccess": "Y",
                            "license": "CC BY",
                        }
                    ]
                }
            },
        )

    with FullTextAuditService(settings, transport=httpx.MockTransport(handler)) as service:
        report = service.audit(
            [_record("record-1", "10.1371/example")],
            include_slow_fallbacks=False,
        )

    observation = report.records[0].observations[0]
    assert report.resolved_count == 0
    assert observation.state == "available"
    assert observation.candidate is None
    assert observation.native_candidates == [
        NativeFullTextCandidate(
            doi="10.1371/example",
            source="europe_pmc",
            format="jats_xml",
            provider_id="PMC123",
            url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML",
            media_type="application/xml",
            license="CC BY",
        )
    ]


def test_istex_detects_tei_and_cleaned_full_text_without_pdf(settings, monkeypatch) -> None:
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

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "ISTEX123",
                        "doi": ["10.1371/example"],
                        "fulltext": [
                            {"extension": "tei", "mimetype": "application/tei+xml"},
                            {"extension": "cleaned", "mimetype": "text/plain"},
                        ],
                    }
                ]
            },
        )

    with FullTextAuditService(settings, transport=httpx.MockTransport(handler)) as service:
        report = service.audit(
            [_record("record-1", "10.1371/example")],
            include_slow_fallbacks=False,
        )

    observation = report.records[0].observations[0]
    assert observation.state == "available"
    assert observation.candidate is None
    assert [candidate.format for candidate in observation.native_candidates] == [
        "tei_xml",
        "cleaned_text",
    ]


def test_doaj_accepts_only_a_declared_structured_full_text_link(settings) -> None:
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["doaj"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
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
                                    "content_type": "application/xml",
                                    "url": "https://journal.example/article.xml",
                                },
                                {
                                    "type": "abstract",
                                    "content_type": "application/xml",
                                    "url": "https://journal.example/abstract.xml",
                                },
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

    native_candidates = report.records[0].observations[0].native_candidates
    assert [candidate.format for candidate in native_candidates] == ["structured_xml"]
    assert native_candidates[0].url == "https://journal.example/article.xml"


def test_crossref_accepts_an_explicit_typed_text_mining_link(settings) -> None:
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["crossref"],
                request_delay_seconds=0,
                max_retries=0,
            )
        }
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "link": [
                        {
                            "content-type": "text/plain",
                            "URL": "https://publisher.example/article.txt",
                        }
                    ]
                }
            },
        )

    with FullTextAuditService(settings, transport=httpx.MockTransport(handler)) as service:
        report = service.audit([_record("record-1", "10.1371/example")])

    native_candidates = report.records[0].observations[0].native_candidates
    assert [candidate.format for candidate in native_candidates] == ["plain_text"]
    assert native_candidates[0].url == "https://publisher.example/article.txt"


def test_native_asset_is_persisted_and_cached(settings) -> None:
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_record(database, "record-1", "10.1371/example")
    store = FullTextStore(database)
    candidate = NativeFullTextCandidate(
        doi="10.1371/example",
        source="europe_pmc",
        format="jats_xml",
        provider_id="PMC123",
        url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML",
        media_type="application/xml",
        license="CC BY",
    )

    store.upsert_observation(
        _record("record-1", "10.1371/example"),
        FullTextObservation(
            source="europe_pmc",
            state="available",
            native_candidates=[candidate],
        ),
    )

    with database.connect() as connection:
        row = connection.execute(
            "SELECT doi, source, format, state FROM native_full_text_assets"
        ).fetchone()
    assert tuple(row) == ("10.1371/example", "europe_pmc", "jats_xml", "available")
    cached = store.cached_observations(max_age_hours=24, sources=["europe_pmc"])
    assert cached["10.1371/example"]["europe_pmc"].native_candidates == [candidate]


def test_native_downloader_is_atomic_and_uses_istex_bearer_token(settings, monkeypatch) -> None:
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
    captured_headers: dict[str, str] = {}

    def fake_stream(_self, _source, url, destination, headers):
        captured_headers.update(headers)
        destination.write_bytes(b"<TEI><text>Full article body.</text></TEI>")
        return url, "application/tei+xml", destination.stat().st_size

    monkeypatch.setattr(FullTextDownloader, "_stream", fake_stream)
    candidate = NativeFullTextCandidate(
        doi="10.1371/example",
        source="istex",
        format="tei_xml",
        provider_id="ISTEX123",
        url="https://api.istex.fr/document/ISTEX123/fulltext/tei",
        media_type="application/tei+xml",
    )

    downloaded = FullTextDownloader(settings).download_native(candidate)

    assert downloaded.path.suffixes[-2:] == [".tei", ".xml"]
    assert downloaded.path.read_text(encoding="utf-8").startswith("<TEI>")
    assert captured_headers["Authorization"] == "Bearer test-token"


def test_harvest_retains_one_native_article_body_without_pdf_ingestion(
    settings,
    monkeypatch,
    tmp_path,
) -> None:
    settings = settings.model_copy(
        update={
            "full_text": FullTextConfig(
                enabled=True,
                sources=["europe_pmc"],
                request_delay_seconds=0,
                max_retries=0,
                max_native_downloads_per_run=1,
            )
        }
    )
    database = Database(settings.paths.database_path)
    database.initialize()
    _insert_record(database, "record-1", "10.1371/example")
    candidate = NativeFullTextCandidate(
        doi="10.1371/example",
        source="europe_pmc",
        format="jats_xml",
        provider_id="PMC123",
        url="https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123/fullTextXML",
        media_type="application/xml",
        license="CC BY",
    )

    class FakeAuditService:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def audit(self, records, **_kwargs):
            record = records[0]
            observation = FullTextObservation(
                source="europe_pmc",
                state="available",
                native_candidates=[candidate],
            )
            return FullTextAuditReport(
                doi_count=1,
                accepted_doi_count=1,
                resolved_count=0,
                resolved_accepted_count=0,
                source_available_counts={"europe_pmc": 1},
                source_authentication_required_counts={},
                source_errors={},
                records=[
                    FullTextAuditRecord(
                        record_id=str(record["id"]),
                        doi=str(record["doi"]),
                        title=str(record["title"]),
                        relevance_status=str(record["relevance_status"]),
                        relevance_theme=str(record["relevance_theme"]),
                        observations=[observation],
                    )
                ],
            )

    asset_path = tmp_path / "article.jats.xml"
    asset_path.write_text("<article><body>Text</body></article>", encoding="utf-8")

    def fake_download(_self, downloaded_candidate):
        assert downloaded_candidate == candidate
        return DownloadedFullText(
            path=asset_path,
            final_url=downloaded_candidate.url,
            sha256="a" * 64,
            byte_count=asset_path.stat().st_size,
            media_type="application/xml",
        )

    monkeypatch.setattr("app.updates.full_text.FullTextAuditService", FakeAuditService)
    monkeypatch.setattr(FullTextDownloader, "download_native", fake_download)

    _audit, harvest = FullTextHarvestService(settings, database).run()

    assert harvest.native_downloaded == 1
    assert harvest.ingested == 0
    with database.connect() as connection:
        state = connection.execute("SELECT state FROM native_full_text_assets").fetchone()[0]
    assert state == "downloaded"
