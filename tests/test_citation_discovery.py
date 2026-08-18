from __future__ import annotations

import json

import httpx

from app.updates.opencitations import OpenCitationsClient
from scripts.harvest_citation_discovery import _balanced_seeds, _resolve_pending


def test_opencitations_maps_graph_edges_and_metadata(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/citations/" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "oci": "1-2",
                        "citing": "omid:br/1 doi:10.1000/related pmid:1",
                        "cited": "doi:10.1000/seed",
                        "creation": "2025-01-02",
                    },
                    {
                        "oci": "3-2",
                        "citing": "doi:10.1000/related doi:10.1000/preprint",
                        "cited": "doi:10.1000/seed",
                    },
                ],
            )
        if "/metadata/" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "doi:10.1000/related openalex:W1 omid:br/1",
                        "title": "Cider yeast fermentation ecology",
                        "author": "Doe, Jane [orcid:0000-0000]; Smith, John [omid:ra/1]",
                        "pub_date": "2025-01-02",
                        "venue": "Fermentation Journal [issn:1234-5678]",
                        "type": "journal article",
                        "publisher": "Example Press [crossref:1]",
                    }
                ],
            )
        raise AssertionError(str(request.url))

    client = OpenCitationsClient(settings, transport=httpx.MockTransport(handler))
    client._pace = lambda: None
    try:
        edges = client.relations("10.1000/seed", "citation")
        records = client.lookup_dois(["10.1000/related"])
    finally:
        client.close()

    assert [edge.related_doi for edge in edges] == [
        "10.1000/related",
        "10.1000/preprint",
    ]
    assert edges[0].seed_doi == "10.1000/seed"
    assert records[0].doi == "10.1000/related"
    assert records[0].authors == ["Doe, Jane", "Smith, John"]
    assert records[0].journal == "Fermentation Journal"
    assert records[0].publication_year == 2025


def test_seed_selection_round_robins_themes() -> None:
    rows = [
        {"doi": "10.1000/a1", "theme": "a", "title": "A1"},
        {"doi": "10.1000/a2", "theme": "a", "title": "A2"},
        {"doi": "10.1000/b1", "theme": "b", "title": "B1"},
        {"doi": "10.1000/b2", "theme": "b", "title": "B2"},
    ]

    seeds = _balanced_seeds(rows, 4)

    assert [seed["doi"] for seed in seeds] == [
        "10.1000/a1",
        "10.1000/b1",
        "10.1000/a2",
        "10.1000/b2",
    ]


def test_pending_resolution_keeps_citation_provenance(tmp_path) -> None:
    class FakeClient:
        def lookup_dois(self, dois):
            from app.updates.models import BibliographicRecord

            assert dois == ["10.1000/cider"]
            return [
                BibliographicRecord(
                    source="OpenCitations Meta",
                    source_id="10.1000/cider",
                    title="Apple cider fermentation microbiology",
                    doi="10.1000/cider",
                )
            ]

    decisions = tmp_path / "decisions.jsonl"
    results = tmp_path / "results.jsonl"
    pending = {
        "10.1000/cider": [
            {
                "seed_doi": "10.1000/seed",
                "related_doi": "10.1000/cider",
                "relation": "citation",
            }
        ]
    }
    decided: set[str] = set()
    result_dois: set[str] = set()

    _resolve_pending(FakeClient(), pending, decided, result_dois, decisions, results, 25)

    row = json.loads(results.read_text(encoding="utf-8"))
    assert row["seed_doi"] == "10.1000/seed"
    assert row["relation"] == "citation"
    assert row["citation_provider"] == "OpenCitations Index v2"
    assert decided == {"10.1000/cider"}
