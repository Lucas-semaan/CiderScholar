from __future__ import annotations

import httpx

from app.config import Settings
from app.updates.clarivate import ClarivateClient
from app.updates.core import CoreClient
from app.updates.crossref import CrossrefClient
from app.updates.datacite import DataCiteClient
from app.updates.doaj import DoajClient
from app.updates.elsevier import ElsevierClient
from app.updates.europe_pmc import EuropePmcClient
from app.updates.hal import HalClient
from app.updates.istex import IstexClient
from app.updates.models import BibliographicRecord
from app.updates.openaire import OpenAireClient
from app.updates.openalex import OpenAlexClient
from app.updates.pubag import PubAgClient
from app.updates.pubmed import PubMedClient
from app.updates.semantic_scholar import SemanticScholarClient
from app.updates.service import CLIENTS, BibliographicDiscoveryService
from app.updates.zenodo import ZenodoClient


def test_default_bibliographic_sources_match_the_client_registry(settings) -> None:
    assert set(settings.bibliographic.sources) == set(CLIENTS)


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


def test_crossref_exact_doi_lookup_normalizes_deduplicates_and_checks_identity(settings) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        doi = "10.1000/a" if request.url.path.endswith("10.1000/a") else "10.1000/b"
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": doi,
                    "title": [f"Cider work {doi[-1]}"],
                    "abstract": "A validated cider fermentation abstract.",
                }
            },
        )

    with CrossrefClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.lookup_dois(["10.1000/A", "https://doi.org/10.1000/a", "10.1000/b"])

    assert requested == ["/works/10.1000/a", "/works/10.1000/b"]
    assert [record.doi for record in records] == ["10.1000/a", "10.1000/b"]


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


def test_pubmed_search_fetches_and_maps_structured_abstracts(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/esearch.fcgi"):
            assert request.url.params["db"] == "pubmed"
            assert request.url.params["retstart"] == "50"
            assert request.url.params["retmax"] == "25"
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["123456"]}},
            )
        assert request.url.path.endswith("/efetch.fcgi")
        assert request.url.params["id"] == "123456"
        return httpx.Response(
            200,
            text="""<?xml version="1.0"?>
            <PubmedArticleSet><PubmedArticle>
              <MedlineCitation><PMID>123456</PMID><Article>
                <ArticleTitle>Cider <i>yeast</i> ecology</ArticleTitle>
                <Abstract>
                  <AbstractText Label="BACKGROUND">Apple microbiota were profiled.</AbstractText>
                  <AbstractText Label="RESULTS">Fermentation was reproducible.</AbstractText>
                </Abstract>
                <AuthorList>
                  <Author><ForeName>Ada</ForeName><LastName>Test</LastName></Author>
                  <Author><CollectiveName>Cider Consortium</CollectiveName></Author>
                </AuthorList>
                <Journal><Title>Food Microbiology</Title><JournalIssue><PubDate>
                  <MedlineDate>2024 Jan-Feb</MedlineDate>
                </PubDate></JournalIssue></Journal>
                <PublicationTypeList>
                  <PublicationType>Journal Article</PublicationType>
                </PublicationTypeList>
                <ELocationID EIdType="doi">10.1000/PUBMED</ELocationID>
              </Article></MedlineCitation>
              <PubmedData><ArticleIdList>
                <ArticleId IdType="doi">10.1000/PUBMED</ArticleId>
              </ArticleIdList></PubmedData>
            </PubmedArticle></PubmedArticleSet>""",
        )

    with PubMedClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider fermentation", 25, offset=50)

    assert records[0].source_id == "123456"
    assert records[0].title == "Cider yeast ecology"
    assert records[0].abstract == (
        "BACKGROUND: Apple microbiota were profiled. RESULTS: Fermentation was reproducible."
    )
    assert records[0].authors == ["Ada Test", "Cider Consortium"]
    assert records[0].journal == "Food Microbiology"
    assert records[0].publication_year == 2024
    assert records[0].doi == "10.1000/pubmed"


def test_pubag_search_uses_public_primo_without_aureli_token(settings, monkeypatch) -> None:
    monkeypatch.setenv("CIDERSCHOLAR_AURELI_SESSION_TOKEN", "must-not-leak")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/primaws/rest/pub/pnxs"
        assert request.headers.get("authorization") is None
        assert request.url.params["vid"] == "01NAL_INST:MAIN"
        assert request.url.params["scope"] == "pubag"
        assert request.url.params["offset"] == "50"
        return httpx.Response(
            200,
            json={
                "docs": [
                    {
                        "context": "L",
                        "pnx": {
                            "control": {"recordid": ["alma991"]},
                            "display": {
                                "title": ["Microbial ecology of cider fermentation"],
                                "type": ["article"],
                                "description": ["Yeasts shaped aroma and fermentation kinetics."],
                            },
                            "search": {"creatorcontrib": ["Ada Test"]},
                            "facets": {"creationdate": ["2025"]},
                            "addata": {
                                "doi": ["10.1000/PUBAG"],
                                "jtitle": ["Food Microbiology"],
                            },
                        },
                    }
                ]
            },
        )

    with PubAgClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider fermentation", 50, offset=50)

    assert records[0].source == "USDA PubAg"
    assert records[0].source_id == "alma991"
    assert records[0].abstract == "Yeasts shaped aroma and fermentation kinetics."
    assert records[0].publication_year == 2025
    assert records[0].doi == "10.1000/pubag"
    assert records[0].url is not None
    assert records[0].url.startswith("https://search.nal.usda.gov/discovery/fulldisplay?")


def test_hal_maps_public_repository_metadata_and_offset(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/"
        assert request.url.params["start"] == "50"
        assert request.url.params["rows"] == "25"
        return httpx.Response(
            200,
            json={
                "response": {
                    "docs": [
                        {
                            "docid": 42,
                            "halId_s": "hal-00000042",
                            "title_s": ["Fermentation du cidre"],
                            "abstract_s": ["Un résultat local traçable."],
                            "authFullName_s": ["Ada Test", "Ada Test", "B Demo"],
                            "journalTitle_s": "Revue du cidre",
                            "producedDateY_i": 2024,
                            "doiId_s": "https://doi.org/10.1000/HAL",
                            "uri_s": "https://hal.science/hal-00000042",
                            "docType_s": "ART",
                        }
                    ]
                }
            },
        )

    with HalClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 25, offset=50)

    assert records[0].source_id == "hal-00000042"
    assert records[0].doi == "10.1000/hal"
    assert records[0].authors == ["Ada Test", "B Demo"]
    assert records[0].abstract == "Un résultat local traçable."


def test_core_uses_public_mode_or_optional_key_and_maps_metadata(settings, monkeypatch) -> None:
    seen_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("authorization"))
        assert request.url.params["offset"] == "100"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 17491096,
                        "title": "Cider organic acids",
                        "abstract": "Malic acid changed during fermentation.",
                        "authors": [{"name": "F. Zhou"}, {"name": "F. Zhou"}],
                        "doi": "10.1000/core",
                        "yearPublished": 2008,
                        "citationCount": 3,
                        "journals": [{"title": "Food Science"}],
                        "documentType": "research",
                        "downloadUrl": "https://core.ac.uk/download/42.pdf",
                    }
                ]
            },
        )

    monkeypatch.delenv("CORE_API_KEY", raising=False)
    with CoreClient(settings, transport=httpx.MockTransport(handler)) as client:
        public_records = client.search("cider", 50, offset=100)
    monkeypatch.setenv("CORE_API_KEY", "core-test-key")
    with CoreClient(settings, transport=httpx.MockTransport(handler)) as client:
        client.search("cider", 50, offset=100)

    assert seen_authorization == [None, "Bearer core-test-key"]
    assert public_records[0].source_id == "17491096"
    assert public_records[0].authors == ["F. Zhou"]
    assert public_records[0].journal == "Food Science"


def test_core_public_search_enforces_provider_pacing(settings, monkeypatch) -> None:
    clock = iter((100.0, 100.0, 101.0, 110.0))
    sleeps: list[float] = []
    monkeypatch.setattr("app.updates.base.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("app.updates.base.time.sleep", sleeps.append)

    client = CoreClient(
        settings,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
    )
    try:
        client._pace()
        client._pace()
    finally:
        client.close()

    assert sleeps == [9.0]


def test_doaj_maps_open_access_article_and_pages_from_offset(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/cider fermentation")
        assert request.url.params["page"] == "3"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "doaj-1",
                        "bibjson": {
                            "title": "Mixed culture cider",
                            "abstract": "Yeast and bacteria shaped cider quality.",
                            "year": "2023",
                            "identifier": [{"type": "doi", "id": "10.1000/DOAJ"}],
                            "author": [
                                {"name": "A Test"},
                                {"name": "A Test"},
                            ],
                            "journal": {
                                "title": "Open Fermentation",
                                "publisher": "Open Publisher",
                            },
                            "link": [
                                {
                                    "type": "fulltext",
                                    "url": "https://example.test/article",
                                }
                            ],
                        },
                    }
                ]
            },
        )

    with DoajClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider fermentation", 50, offset=100)

    assert records[0].doi == "10.1000/doaj"
    assert records[0].authors == ["A Test"]
    assert records[0].publisher == "Open Publisher"
    assert records[0].url == "https://example.test/article"


def test_semantic_scholar_uses_optional_key_and_maps_papers(settings, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "semantic-test-key"
        assert request.url.params["offset"] == "25"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paperId": "s2-paper",
                        "title": "Cider yeast ecology",
                        "abstract": "A traceable abstract.",
                        "authors": [{"name": "Ada Test"}],
                        "venue": "Fermentation",
                        "year": 2025,
                        "externalIds": {"DOI": "10.1000/S2"},
                        "citationCount": 8,
                        "url": "https://www.semanticscholar.org/paper/s2-paper",
                        "publicationTypes": ["JournalArticle"],
                    }
                ]
            },
        )

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "semantic-test-key")
    with SemanticScholarClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 25, offset=25)

    assert records[0].doi == "10.1000/s2"
    assert records[0].work_type == "JournalArticle"
    assert records[0].citation_count == 8


def test_istex_maps_public_metadata_search_without_full_text_token(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/document/"
        assert request.url.params["from"] == "50"
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "ISTEX123",
                        "title": "Cider apple diversity",
                        "abstract": "Cultivar knowledge was compared.",
                        "author": [{"name": "Ada Test"}],
                        "publicationDate": "2009",
                        "doi": ["10.1000/ISTEX"],
                        "host": {"title": "Economic Botany"},
                        "genre": ["research-article"],
                        "score": 17.2,
                    }
                ]
            },
        )

    with IstexClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 50, offset=50)

    assert records[0].doi == "10.1000/istex"
    assert records[0].journal == "Economic Botany"
    assert records[0].publication_year == 2009
    assert records[0].relevance_score == 17.2


def test_datacite_maps_doi_metadata_and_abstract(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["page[number]"] == "3"
        assert request.url.params["page[size]"] == "50"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "10.5281/zenodo.42",
                        "attributes": {
                            "doi": "10.5281/ZENODO.42",
                            "titles": [{"title": "Apple cider vinegar"}],
                            "descriptions": [
                                {
                                    "descriptionType": "Abstract",
                                    "description": "Acetic fermentation was optimized.",
                                }
                            ],
                            "creators": [
                                {"name": "Ada Test"},
                                {"name": "Ada Test"},
                            ],
                            "publisher": "Zenodo",
                            "container": {"title": "Fermentation"},
                            "publicationYear": 2024,
                            "types": {"resourceTypeGeneral": "JournalArticle"},
                            "citationCount": 2,
                            "url": "https://zenodo.org/doi/10.5281/zenodo.42",
                        },
                    }
                ]
            },
        )

    with DataCiteClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 50, offset=100)

    assert records[0].doi == "10.5281/zenodo.42"
    assert records[0].abstract == "Acetic fermentation was optimized."
    assert records[0].authors == ["Ada Test"]
    assert records[0].work_type == "JournalArticle"


def test_openaire_maps_publication_graph_metadata(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graph/v3/research-products"
        assert request.url.params["type"] == "publication"
        assert request.url.params["page"] == "2"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "openaire-1",
                        "mainTitle": "Optimization of cider fermentation",
                        "descriptions": ["Fermentation time affected total acidity."],
                        "authors": [
                            {"fullName": "Ada Test"},
                            {"fullName": "Ada Test"},
                        ],
                        "publicationDate": "2020-01-01",
                        "publisher": "Open Publisher",
                        "container": {"name": "Food Journal"},
                        "pids": [{"scheme": "doi", "value": "10.1000/OPENAIRE"}],
                        "type": "publication",
                        "instances": [
                            {
                                "type": "Article",
                                "urls": ["https://example.test/article"],
                            }
                        ],
                        "indicators": {"citationImpact": {"citationCount": 4}},
                    }
                ]
            },
        )

    with OpenAireClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 50, offset=50)

    assert records[0].doi == "10.1000/openaire"
    assert records[0].authors == ["Ada Test"]
    assert records[0].journal == "Food Journal"
    assert records[0].citation_count == 4


def test_zenodo_maps_publication_metadata(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/records"
        assert request.url.params["type"] == "publication"
        assert request.url.params["size"] == "25"
        assert request.url.params["page"] == "2"
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "id": 3561247,
                            "doi": "10.5281/ZENODO.3561247",
                            "metadata": {
                                "title": "Preparation of apple cider vinegar",
                                "description": "<p>Apple cider vinegar was fermented.</p>",
                                "publication_date": "2019-12-04",
                                "creators": [
                                    {"name": "Ada Test"},
                                    {"name": "Ada Test"},
                                ],
                                "resource_type": {
                                    "title": "Journal article",
                                    "type": "publication",
                                },
                                "journal": {"title": "Dagon Research Journal"},
                            },
                            "links": {"self_html": "https://zenodo.org/records/3561247"},
                        }
                    ]
                }
            },
        )

    with ZenodoClient(settings, transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", 50, offset=50)

    assert records[0].doi == "10.5281/zenodo.3561247"
    assert records[0].abstract == "Apple cider vinegar was fermented."
    assert records[0].authors == ["Ada Test"]
    assert records[0].journal == "Dagon Research Journal"


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
