from __future__ import annotations

import json

import httpx
import pytest

from app.updates.aureli import AureliClient


def _payload() -> dict[str, object]:
    return {
        "info": {"total": 1831, "first": 1, "last": 2},
        "docs": [
            {
                "context": "PC",
                "pnx": {
                    "control": {"recordid": ["cdi_test_primary_1"]},
                    "display": {
                        "title": ["Chemical characterization of cider fermentation"],
                        "creator": ["Ada Author ; Bob Researcher"],
                        "identifier": ["ISSN: 1234-5678", "DOI: 10.1000/CIDER.1"],
                        "type": ["Article"],
                        "description": ["<p>A cider yeast fermentation abstract.</p>"],
                    },
                    "addata": {
                        "date": ["2024-09-01"],
                        "jtitle": ["Journal of Cider Science"],
                        "pub": ["Scientific Publisher"],
                    },
                    "facets": {"creationdate": ["2024"]},
                    "search": {"rsrctype": ["article"]},
                },
            },
            {"context": "PC", "pnx": {"control": {"recordid": ["missing-title"]}}},
        ],
    }


def test_aureli_search_uses_article_year_and_full_text_filters(settings) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params.multi_items()))
        return httpx.Response(200, content=json.dumps(_payload()).encode())

    with AureliClient(settings, transport=httpx.MockTransport(handler)) as client:
        page = client.search_articles("cider", year=2024, limit=2, offset=0)

    assert captured["q"] == "any,contains,cider"
    assert captured["searchInFulltextUserSelection"] == "true"
    assert captured["qInclude"] == (
        "facet_rtype,exact,articles|,|facet_searchcreationdate,exact,[2024 TO 2024]"
    )
    assert page.total_results == 1831
    assert page.raw_record_count == 2
    assert page.parse_error_count == 1
    assert len(page.records) == 1
    record = page.records[0]
    assert record.source == "Aureli"
    assert record.source_id == "cdi_test_primary_1"
    assert record.doi == "10.1000/cider.1"
    assert record.publication_year == 2024
    assert record.authors == ["Ada Author", "Bob Researcher"]
    assert record.abstract == "A cider yeast fermentation abstract."
    assert "docid=cdi_test_primary_1" in str(record.url)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"query": "cider;malicious", "year": 2024}, "query separators"),
        ({"query": "cider", "year": 1599}, "publication year"),
        ({"query": "cider", "year": 2024, "limit": 51}, "page size"),
        ({"query": "cider", "year": 2024, "offset": 201}, "offset"),
    ],
)
def test_aureli_search_rejects_unbounded_inputs(settings, kwargs, message) -> None:
    with (
        AureliClient(
            settings, transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ) as client,
        pytest.raises(ValueError, match=message),
    ):
        client.search_articles(**kwargs)


def test_aureli_generic_search_requires_an_explicit_year(settings) -> None:
    with (
        AureliClient(
            settings, transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ) as client,
        pytest.raises(ValueError, match=r"search_articles\(\)"),
    ):
        client.search("cider", 10)


def test_aureli_authenticated_session_allows_deep_offsets(settings, monkeypatch) -> None:
    captured_authorization = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_authorization
        captured_authorization = request.headers.get("Authorization")
        return httpx.Response(200, json={"info": {"total": 1831}, "docs": []})

    monkeypatch.setenv("CIDERSCHOLAR_AURELI_SESSION_TOKEN", "campaign-token")
    with AureliClient(settings, transport=httpx.MockTransport(handler)) as client:
        page = client.search_articles("cider", year=2024, limit=1, offset=250)

    assert page.total_results == 1831
    assert captured_authorization == "Bearer campaign-token"


def test_aureli_authenticated_session_rejects_offsets_beyond_primo_cap(
    settings, monkeypatch
) -> None:
    monkeypatch.setenv("CIDERSCHOLAR_AURELI_SESSION_TOKEN", "campaign-token")
    with (
        AureliClient(
            settings, transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ) as client,
        pytest.raises(ValueError, match="1950"),
    ):
        client.search_articles("cider", year=2024, limit=1, offset=2000)
