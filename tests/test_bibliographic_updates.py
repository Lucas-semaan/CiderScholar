from __future__ import annotations

import httpx

from app.config import Settings
from app.updates.clarivate import ClarivateClient
from app.updates.crossref import CrossrefClient
from app.updates.elsevier import ElsevierClient
from app.updates.europe_pmc import EuropePmcClient
from app.updates.models import BibliographicRecord
from app.updates.openalex import OpenAlexClient
from app.updates.service import BibliographicDiscoveryService


def test_crossref_skips_invalid_candidate_without_losing_valid_results(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.1000/invalid-year",
                            "title": ["Invalid historical candidate"],
                            "issued": {"date-parts": [[1400]]},
                        },
                        {
                            "DOI": "10.1000/valid",
                            "title": ["Valid cider candidate"],
                            "issued": {"date-parts": [[2020]]},
                            "type": "journal-article",
                        },
                    ]
                }
            },
        )

    with CrossrefClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 5)

    assert [record.doi for record in records] == ["10.1000/valid"]


def test_openalex_search_uses_migrated_key_and_maps_pages(settings, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_KEY", "openalex-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/works"
        assert request.url.params["api_key"] == "openalex-test-key"
        assert request.url.params["page"] == "3"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "title": "Fermentation temperature",
                        "doi": "https://doi.org/10.1000/test",
                        "publication_year": 2025,
                        "cited_by_count": 7,
                        "relevance_score": 12.5,
                        "abstract_inverted_index": {
                            "Local": [0],
                            "evidence": [1],
                        },
                        "authorships": [{"author": {"display_name": "Ada Test"}}],
                        "primary_location": {
                            "landing_page_url": "https://example.test/work",
                            "source": {
                                "display_name": "Test Journal",
                                "host_organization_name": "Test Publisher",
                            },
                        },
                        "type": "article",
                    }
                ]
            },
        )

    with OpenAlexClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("fermentation", 5, offset=10)

    assert len(records) == 1
    assert records[0].doi == "10.1000/test"
    assert records[0].authors == ["Ada Test"]
    assert records[0].abstract == "Local evidence"
    assert records[0].journal == "Test Journal"
    assert records[0].work_type == "article"
    assert records[0].publisher == "Test Publisher"


def test_openalex_batches_doi_lookups_in_one_filtered_request(settings, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_KEY", "openalex-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["filter"] == "doi:10.1000/a|10.1000/b"
        assert request.url.params["per_page"] == "2"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "title": "Cider nitrogen",
                        "doi": "https://doi.org/10.1000/a",
                        "abstract_inverted_index": {"Cider": [0], "nitrogen": [1]},
                    }
                ]
            },
        )

    with OpenAlexClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.lookup_dois(["10.1000/A", "10.1000/b", "10.1000/a"])

    assert len(records) == 1
    assert records[0].doi == "10.1000/a"
    assert records[0].abstract == "Cider nitrogen"


def test_openalex_batches_work_id_lookups_in_one_filtered_request(settings, monkeypatch) -> None:
    monkeypatch.setenv("OPENALEX_KEY", "openalex-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["filter"] == "openalex_id:W123|W456"
        assert request.url.params["per_page"] == "2"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "title": "A cider thesis",
                        "type": "dissertation",
                        "publication_year": 2020,
                    }
                ]
            },
        )

    with OpenAlexClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.lookup_ids(["w123", "W456", "W123", "invalid"])

    assert records[0].source_id == "W123"
    assert records[0].work_type == "dissertation"


def test_europe_pmc_maps_core_metadata_without_a_key(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["resultType"] == "core"
        return httpx.Response(
            200,
            json={
                "resultList": {
                    "result": [
                        {
                            "id": "MED:1",
                            "title": "Cider microbiology",
                            "authorString": "A Test, B Demo",
                            "pubYear": "2024",
                            "journalTitle": "Microbiology",
                            "doi": "10.1000/pmc",
                            "citedByCount": 3,
                            "pmid": "123",
                            "abstractText": "A local abstract.",
                        }
                    ]
                }
            },
        )

    with EuropePmcClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 5)

    assert records[0].publication_year == 2024
    assert records[0].authors == ["A Test", "B Demo"]
    assert records[0].url == "https://doi.org/10.1000/pmc"


def test_elsevier_and_clarivate_keep_keys_in_headers(settings, monkeypatch) -> None:
    monkeypatch.setenv("ELSEVIER_KEY", "elsevier-test-key")
    monkeypatch.setenv("CLARIVATE_API_KEY", "clarivate-test-key")
    seen_headers: list[tuple[str, str]] = []

    def elsevier_handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(("elsevier", request.headers["x-els-apikey"]))
        return httpx.Response(
            200,
            json={
                "search-results": {
                    "entry": [
                        {
                            "dc:identifier": "SCOPUS_ID:1",
                            "dc:title": "Scopus result",
                            "prism:doi": "10.1000/scopus",
                            "prism:coverDate": "2023-01-01",
                        }
                    ]
                }
            },
        )

    def clarivate_handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(("clarivate", request.headers["x-apikey"]))
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "uid": "WOS:1",
                        "title": "Web of Science result",
                        "source": {"publishYear": 2022},
                        "identifiers": {"doi": "10.1000/wos"},
                    }
                ]
            },
        )

    with ElsevierClient(settings, transport=httpx.MockTransport(elsevier_handler)) as elsevier:
        elsevier_records = elsevier.search("cider", 1)
    with ClarivateClient(settings, transport=httpx.MockTransport(clarivate_handler)) as clarivate:
        clarivate_records = clarivate.search("cider", 1)

    assert seen_headers == [
        ("elsevier", "elsevier-test-key"),
        ("clarivate", "clarivate-test-key"),
    ]
    assert elsevier_records[0].doi == "10.1000/scopus"
    assert clarivate_records[0].doi == "10.1000/wos"


def test_clarivate_expanded_search_and_mapping(monkeypatch) -> None:
    monkeypatch.setenv("CLARIVATE_API_KEY", "clarivate-test-key")
    settings = Settings.model_validate(
        {
            "app": {
                "offline_mode": False,
                "allow_bibliographic_apis": True,
            },
            "bibliographic": {
                "enabled": True,
                "sources": ["clarivate"],
                "clarivate_api_mode": "expanded",
                "clarivate_base_url": ("https://wos-api.clarivate.com/api/wos"),
            },
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/wos"
        assert request.url.params["usrQuery"] == "TS=(cider fermentation)"
        assert request.headers["x-apikey"] == "clarivate-test-key"
        return httpx.Response(
            200,
            json={
                "Data": {
                    "Records": {
                        "records": {
                            "REC": [
                                {
                                    "UID": "WOS:EXPANDED:1",
                                    "static_data": {
                                        "summary": {
                                            "titles": {
                                                "title": [
                                                    {
                                                        "type": "item",
                                                        "content": "Cider yeasts",
                                                    },
                                                    {
                                                        "type": "source",
                                                        "content": "Fermentation",
                                                    },
                                                ]
                                            },
                                            "pub_info": {"sortdate": "2025-02-14"},
                                            "names": {"name": {"display_name": "Ada Test"}},
                                        },
                                        "fullrecord_metadata": {
                                            "abstracts": {
                                                "abstract": {
                                                    "abstract_text": {"p": "Evidence abstract."}
                                                }
                                            }
                                        },
                                    },
                                    "dynamic_data": {
                                        "cluster_related": {
                                            "identifiers": {
                                                "identifier": {
                                                    "type": "doi",
                                                    "value": "10.1000/expanded",
                                                }
                                            }
                                        },
                                        "citation_related": {
                                            "tc_list": {
                                                "silo_tc": {
                                                    "coll_id": "WOS",
                                                    "local_count": 9,
                                                }
                                            }
                                        },
                                    },
                                }
                            ]
                        }
                    }
                }
            },
        )

    with ClarivateClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider fermentation", 3)

    assert len(records) == 1
    assert records[0].source == "Clarivate Web of Science Expanded"
    assert records[0].title == "Cider yeasts"
    assert records[0].journal == "Fermentation"
    assert records[0].authors == ["Ada Test"]
    assert records[0].publication_year == 2025
    assert records[0].doi == "10.1000/expanded"
    assert records[0].citation_count == 9


def test_discovery_is_sequential_deduplicated_and_failure_isolated(settings, monkeypatch) -> None:
    active = settings.model_copy(deep=True)
    active.app.offline_mode = False
    active.app.allow_bibliographic_apis = True
    active.bibliographic.enabled = True
    active.bibliographic.sources = ["crossref", "openalex", "clarivate"]
    calls: list[str] = []

    class FakeCrossref:
        def __init__(self, _settings) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def search(self, _query: str, _limit: int) -> list[BibliographicRecord]:
            calls.append("crossref")
            return [
                BibliographicRecord(
                    source="Crossref",
                    source_id="1",
                    title="Same article",
                    doi="10.1000/same",
                )
            ]

    class FakeOpenAlex(FakeCrossref):
        def search(self, _query: str, _limit: int) -> list[BibliographicRecord]:
            calls.append("openalex")
            return [
                BibliographicRecord(
                    source="OpenAlex",
                    source_id="2",
                    title="Same article elsewhere",
                    doi="10.1000/same",
                )
            ]

    class FakeClarivate(FakeCrossref):
        def search(self, _query: str, _limit: int) -> list[BibliographicRecord]:
            calls.append("clarivate")
            raise RuntimeError("isolated failure")

    monkeypatch.setattr(
        "app.updates.service.CLIENTS",
        {
            "crossref": FakeCrossref,
            "openalex": FakeOpenAlex,
            "clarivate": FakeClarivate,
        },
    )

    report = BibliographicDiscoveryService(active).search(" cider ", limit_per_source=2)

    assert calls == ["crossref", "openalex", "clarivate"]
    assert len(report.records) == 1
    assert report.successful_sources == ["crossref", "openalex"]
    assert report.errors[0].source == "clarivate"
