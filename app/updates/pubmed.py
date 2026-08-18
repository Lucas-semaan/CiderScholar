"""Opt-in official PubMed discovery through NCBI E-utilities."""

from __future__ import annotations

import re
from xml.etree import ElementTree

from app.updates.base import BibliographicApiError, OfficialBibliographicClient
from app.updates.models import BibliographicRecord, clean_text, normalize_doi

YEAR_PATTERN = re.compile(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b")


class PubMedClient(OfficialBibliographicClient):
    source_id = "pubmed"
    source_label = "PubMed"
    minimum_request_delay_seconds = 0.35

    def search(
        self,
        query: str,
        limit: int,
        *,
        offset: int = 0,
    ) -> list[BibliographicRecord]:
        page_size = min(max(limit, 1), 100)
        common: dict[str, str | int] = {
            "db": "pubmed",
            "tool": "CiderScholar",
        }
        if self.config.pubmed_email:
            common["email"] = self.config.pubmed_email
        search_payload = self._get_json(
            f"{self.config.pubmed_base_url}/esearch.fcgi",
            params={
                **common,
                "term": query,
                "retmode": "json",
                "retmax": page_size,
                "retstart": max(offset, 0),
                "sort": "relevance",
            },
        )
        search_result = search_payload.get("esearchresult")
        id_list = search_result.get("idlist") if isinstance(search_result, dict) else None
        identifiers = [str(value) for value in id_list or [] if str(value).isdigit()]
        if not identifiers:
            return []
        xml_text = self._get_text(
            f"{self.config.pubmed_base_url}/efetch.fcgi",
            params={
                **common,
                "id": ",".join(identifiers),
                "retmode": "xml",
            },
        )
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise BibliographicApiError("PubMed returned invalid XML") from exc
        records: list[BibliographicRecord] = []
        for element in root.findall(".//PubmedArticle"):
            try:
                records.append(_record(element))
            except ValueError:
                continue
        return records


def _record(element: ElementTree.Element) -> BibliographicRecord:
    citation = element.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    if citation is None or article is None:
        raise ValueError("missing PubMed article metadata")
    pmid = _text(citation.find("PMID"))
    if not pmid:
        raise ValueError("missing PMID")
    doi = _doi(element, article)
    return BibliographicRecord(
        source="PubMed",
        source_id=pmid,
        title=_text(article.find("ArticleTitle")) or "Titre indisponible",
        authors=_authors(article),
        abstract=_abstract(article),
        journal=_text(article.find("Journal/Title")),
        work_type=_text(article.find("PublicationTypeList/PublicationType")),
        publication_year=_publication_year(article),
        doi=doi,
        url=f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        relevance_score=None,
    )


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    return clean_text("".join(element.itertext()))


def _abstract(article: ElementTree.Element) -> str | None:
    parts: list[str] = []
    for element in article.findall("Abstract/AbstractText"):
        text = _text(element)
        if not text:
            continue
        label = clean_text(element.attrib.get("Label"))
        parts.append(f"{label}: {text}" if label else text)
    return clean_text(" ".join(parts))


def _authors(article: ElementTree.Element) -> list[str]:
    names: list[str] = []
    for author in article.findall("AuthorList/Author"):
        collective = _text(author.find("CollectiveName"))
        name = collective or clean_text(
            " ".join(
                value
                for value in (
                    _text(author.find("ForeName")),
                    _text(author.find("LastName")),
                )
                if value
            )
        )
        if name:
            names.append(name)
    return list(dict.fromkeys(names))


def _publication_year(article: ElementTree.Element) -> int | None:
    for path in (
        "ArticleDate/Year",
        "Journal/JournalIssue/PubDate/Year",
        "Journal/JournalIssue/PubDate/MedlineDate",
    ):
        value = _text(article.find(path))
        match = YEAR_PATTERN.search(value or "")
        if match:
            return int(match.group(1))
    return None


def _doi(element: ElementTree.Element, article: ElementTree.Element) -> str | None:
    for identifier in element.findall("PubmedData/ArticleIdList/ArticleId"):
        if identifier.attrib.get("IdType", "").casefold() == "doi":
            doi = normalize_doi(_text(identifier))
            if doi:
                return doi
    for identifier in article.findall("ELocationID"):
        if identifier.attrib.get("EIdType", "").casefold() == "doi":
            doi = normalize_doi(_text(identifier))
            if doi:
                return doi
    return None
