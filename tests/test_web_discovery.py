from __future__ import annotations

import base64
from types import SimpleNamespace

import httpx
import pytest

from app.updates.base import BibliographicApiDeferred
from app.updates.web_discovery import WebSearchClient
from scripts.harvest_web_discovery import _jobs, _preliminary_assessment


def test_bing_html_results_are_unwrapped_and_doi_is_extracted() -> None:
    target = "https://doi.org/10.1000/CIDER"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    raw = f"""
    <html><body><ol id="b_results">
      <li class="b_algo">
        <h2><a href="https://www.bing.com/ck/a?u=a1{encoded}">Cider fermentation</a></h2>
        <div class="b_caption"><p>Research article about apple yeast.</p></div>
      </li>
    </ol></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["first"] == "21"
        return httpx.Response(200, text=raw, headers={"Content-Type": "text/html"})

    with WebSearchClient("bing", transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", page=1, page_size=20)

    assert len(records) == 1
    assert records[0].url == target
    assert records[0].doi == "10.1000/cider"
    assert records[0].snippet == "Research article about apple yeast."


def test_duckduckgo_html_results_are_unwrapped() -> None:
    raw = """
    <html><body>
      <div class="result results_links results_links_deep web-result">
        <h2 class="result__title">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fpaper">
            Apple cider microbiology
          </a>
        </h2>
        <a class="result__snippet">Lactic acid bacteria in cider.</a>
      </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["s"] == "0"
        return httpx.Response(200, text=raw, headers={"Content-Type": "text/html"})

    with WebSearchClient("duckduckgo", transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider")

    assert len(records) == 1
    assert records[0].url == "https://example.test/paper"
    assert records[0].snippet == "Lactic acid bacteria in cider."


def test_brave_html_results_use_semantic_snippet_container() -> None:
    raw = """
    <html><body>
      <div class="snippet random" data-pos="1" data-type="web">
        <div class="result-wrapper">
          <a href="https://example.test/cider-paper">
            <div class="title search-snippet-title" title="Cider yeast ecology">
              Cider yeast ecology
            </div>
          </a>
          <div class="generic-snippet">
            <div class="content">A <strong>research study</strong> of cider fermentation.</div>
          </div>
        </div>
      </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["offset"] == "2"
        return httpx.Response(200, text=raw, headers={"Content-Type": "text/html"})

    with WebSearchClient("brave", transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", page=2)

    assert records[0].title == "Cider yeast ecology"
    assert records[0].snippet == "A research study of cider fermentation."
    assert records[0].url == "https://example.test/cider-paper"


def test_yahoo_html_results_are_unwrapped() -> None:
    target = "https://example.test/cider-paper"
    redirect = (
        "https://r.search.yahoo.com/_ylt=test/RV=2/RE=1/RO=10/RU="
        "https%3A%2F%2Fexample.test%2Fcider-paper/RK=2/RS=test-"
    )
    raw = f"""
    <html><body>
      <div class="dd algo algo-sr relsrch">
        <div class="compTitle">
          <a href="{redirect}">
            <h3 class="title">Cider fermentation kinetics</h3>
          </a>
        </div>
        <div class="compText"><p>A journal study of apple juice fermentation.</p></div>
      </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["b"] == "11"
        return httpx.Response(200, text=raw, headers={"Content-Type": "text/html"})

    with WebSearchClient("yahoo", transport=httpx.MockTransport(handler)) as client:
        records = client.search("cider", page=1)

    assert records[0].title == "Cider fermentation kinetics"
    assert records[0].snippet == "A journal study of apple juice fermentation."
    assert records[0].url == target


def test_html_search_block_is_deferred_without_bypass() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(429, headers={"Content-Type": "text/html"})
    )
    with (
        WebSearchClient("bing", transport=transport) as client,
        pytest.raises(BibliographicApiDeferred, match="HTTP 429"),
    ):
        client.search("cider")


def test_duckduckgo_html_challenge_is_deferred_without_solving_it() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text='<form action="//duckduckgo.com/anomaly.js?cc=botnet"></form>',
            headers={"Content-Type": "text/html"},
        )
    )
    with (
        WebSearchClient("duckduckgo", transport=transport) as client,
        pytest.raises(BibliographicApiDeferred, match="anti-automation challenge"),
    ):
        client.search("cider")


def test_preliminary_filter_keeps_scientific_hit_and_drops_cider_shop() -> None:
    scientific = SimpleNamespace(
        engine="duckduckgo",
        title="The effect of apple juice concentration on cider fermentation",
        snippet="A research study of yeast fermentation and phenolic compounds.",
        url="https://www.mdpi.com/2304-8158/9/10/1401",
        doi=None,
    )
    shop = SimpleNamespace(
        engine="bing",
        title="Fashion starts with a feeling - Cider",
        snippet="Women's clothing and accessories.",
        url="https://www.shopcider.com/",
        doi=None,
    )

    assert _preliminary_assessment(scientific, "biochimie") is not None
    assert _preliminary_assessment(shop, "biochimie") is None


def test_web_discovery_jobs_prioritize_first_pages_across_sources() -> None:
    jobs = _jobs(
        engines=("bing", "duckduckgo"),
        query_sets=("focused", "expanded"),
        pages=2,
    )

    assert jobs
    assert all(job["page"] == 0 for job in jobs[: len(jobs) // 2])
    assert {job["engine"] for job in jobs} == {"bing", "duckduckgo"}
    assert {job["query_set"] for job in jobs} == {"focused", "expanded"}
