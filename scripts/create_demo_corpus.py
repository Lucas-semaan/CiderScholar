"""Generate three fully synthetic scientific PDFs for local demonstrations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from app.config import load_settings

ARTICLES = (
    {
        "filename": "demo_fermentation_temperature.pdf",
        "title": "Temperature and Aroma Formation in Synthetic Cider Fermentation",
        "author": "Alice Exemple; Benoît Démo",
        "year": "2024",
        "pages": (
            "Abstract\nThis entirely synthetic study tests how fermentation temperature affects "
            "aroma formation in a simulated cider matrix. No real participants, samples, or DOI "
            "are associated with this demonstration article.\n\nIntroduction\nTemperature can "
            "alter reaction rates and volatile profiles. The experiment compares three local "
            "temperature conditions using generated measurements only.",
            "Materials and methods\nThree simulated batches were assigned to 12, 16, and 20 "
            "degrees Celsius. Measurements were generated from a fixed deterministic table.\n\n"
            "Results\nThe synthetic ester index was 42 units at 12 degrees, 58 units at 16 "
            "degrees, "
            "and 49 units "
            "at 20 degrees. The central condition therefore had the largest simulated value.",
            "Discussion\nWithin this fictional dataset, an intermediate temperature balanced the "
            "reported aroma indicators. These values must not be treated as empirical findings.\n\n"
            "Conclusion\nThe demonstration supports retrieval tests involving temperature, cider, "
            "fermentation, aroma, and quantitative values.",
        ),
    },
    {
        "filename": "demo_yeast_nitrogen.pdf",
        "title": "Nitrogen Availability and Synthetic Yeast Kinetics",
        "author": "Chloé Fictive; David Exemple",
        "year": "2023",
        "pages": (
            "Abstract\nThis fictional article evaluates nitrogen availability and yeast kinetics "
            "in a generated fermentation dataset. It contains no real DOI and no external "
            "source.\n\n"
            "Introduction\nNitrogen is represented as a controllable input in the synthetic model. "
            "The scientific vocabulary exists solely to exercise multilingual local retrieval.",
            "Materials and methods\nGenerated groups received 60, 120, or 180 milligrams per litre "
            "of simulated assimilable nitrogen. Processing was sequential and deterministic.\n\n"
            "Results\nThe fictional time to completion was 18 days at 60 milligrams per litre, 12 "
            "days at 120 milligrams per litre, and 11 days at 180 milligrams per litre.",
            "Discussion\nThe largest synthetic change occurred between 60 and 120 milligrams per "
            "litre. The smaller subsequent change illustrates a plateau for retrieval tests.\n\n"
            "Conclusion\nIn this fake corpus, nitrogen availability is associated with shorter "
            "simulated fermentation time.",
        ),
    },
    {
        "filename": "demo_polyphenol_storage.pdf",
        "title": "Stockage local et stabilité fictive des polyphénols",
        "author": "Émilie Démonstration; Farid Fictif",
        "year": "2022",
        "pages": (
            "Résumé\nCette étude entièrement fictive décrit la stabilité de polyphénols pendant "
            "un stockage simulé. Elle ne possède aucun DOI et ne rapporte aucune expérience "
            "réelle.\n\n"
            "Introduction\nLa température et la durée de stockage sont utilisées comme concepts de "
            "recherche en français dans un corpus de démonstration local.",
            "Matériels et méthodes\nDes valeurs artificielles ont été attribuées à des durées de "
            "zéro, quatre et huit semaines. Aucun échantillon réel n'a été utilisé.\n\n"
            "Résultats\nL'indice "
            "fictif de polyphénols est passé de 100 à 91 puis 83 unités pendant les huit semaines.",
            "Discussion\nLa diminution simulée est progressive et sert à tester les citations de "
            "valeurs quantitatives avec leurs pages.\n\nConclusion\nDans ce jeu de données "
            "factice, "
            "un stockage plus long correspond à un indice de polyphénols plus faible.",
        ),
    },
)


def create_demo_corpus(destination: Path | None = None) -> list[Path]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - installation concern
        raise RuntimeError("PyMuPDF is required to generate the demo corpus") from exc

    settings = load_settings()
    output_dir = (destination or settings.paths.pdf_dir / "demo").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    for article in ARTICLES:
        output = output_dir / str(article["filename"])
        document = fitz.open()
        document.set_metadata(
            {
                "title": str(article["title"]),
                "author": str(article["author"]),
                "subject": "Corpus scientifique entièrement synthétique et libre",
                "keywords": "synthetic, local, offline, demonstration",
                "creationDate": f"D:{article['year']}0101000000",
            }
        )
        for page_text in article["pages"]:
            page = document.new_page(width=595, height=842)
            page.insert_textbox(
                fitz.Rect(54, 54, 541, 788),
                str(page_text),
                fontsize=11,
                lineheight=1.35,
            )
        document.save(output, garbage=4, deflate=True)
        document.close()
        generated.append(output)
    return generated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate three fully synthetic scientific PDFs for local demonstrations."
    )
    parser.add_argument(
        "--destination",
        type=Path,
        help="Output folder (default: data/pdf/demo from the active configuration)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for generated_path in create_demo_corpus(args.destination):
        print(generated_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
