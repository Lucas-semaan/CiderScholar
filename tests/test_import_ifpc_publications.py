from scripts.import_ifpc_publications import parse_ifpc_catalog


def test_parse_ifpc_catalog_keeps_only_unique_official_pdfs() -> None:
    html = """
    <p>N°64 – décembre 2025</p>
    <a href="/wp-content/uploads/2026/01/CT64-document.pdf">Document IFPC</a>
    <a href="/wp-content/uploads/2026/01/CT64-document.pdf">Doublon</a>
    <a href="https://example.org/foreign.pdf">Externe</a>
    <a href="/actualite/">Page HTML</a>
    <p>N°63 – mai 2024</p>
    <a href="http://www.ifpc.eu/legacy/CT63.pdf">Autre document</a>
    """

    publications = parse_ifpc_catalog(html)

    assert len(publications) == 2
    assert publications[0].title == "Document IFPC"
    assert publications[0].publication_year == 2025
    assert publications[0].url == (
        "https://www.ifpc.eu/wp-content/uploads/2026/01/CT64-document.pdf"
    )
    assert publications[1].publication_year == 2024
    assert publications[1].url == "https://www.ifpc.eu/legacy/CT63.pdf"
