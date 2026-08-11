"""Build and verify a focused full-text bibliography for cider microbiology."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus
from app.database.sqlite import Database

FERMENTATION_TARGET = 55
CONTAMINANT_TARGET = 45

BEVERAGE_ANCHOR = re.compile(
    r"\b(?:hard cider|cider|cidre|apple (?:wine|must|juice)|"
    r"fermented apple (?:beverage|juice)|jus de pomme|mo[uû]t de pomme)\b",
    re.IGNORECASE,
)
APPLE_CONTAMINANT_ANCHOR = re.compile(
    r"\b(?:hard cider|cider|cidre|apple (?:juice|fruit|surface|microbiome|"
    r"microbiota|orchard|peel)|postharvest apple|apple-derived products?)\b",
    re.IGNORECASE,
)
FERMENTATION_PATTERN = re.compile(
    r"\b(?:ferment\w*|yeasts?|saccharomyces|oenococcus|malolactic|"
    r"non[- ]saccharomyces|hanseniaspora|metschnikowia|torulaspora|lachancea|"
    r"starmerella|pichia|kluyveromyces|lactic acid bacter\w*|lactobac\w*|"
    r"lactiplantibacillus|leuconostoc|pediococcus|phageome|phages?|"
    r"microbial (?:community|ecology|succession|diversity|dynamics)|"
    r"microbiome|microbiota)\b",
    re.IGNORECASE,
)
CONTAMINANT_PATTERN = re.compile(
    r"\b(?:spoilage|contamin\w*|pathogen\w*|food safety|penicillium|patulin|"
    r"mycotoxin\w*|alicyclobacillus|zygosaccharomyces|brettanomyces|dekker\w*|"
    r"paecilomyces|blue mo[u]?ld|mo[u]?lds?|fungal (?:community|decay|disease)|"
    r"escherichia|e\.?\s*coli|salmonella|listeria|cryptosporidium|"
    r"microbial inactivation|inactivat\w*|pasteuri[sz]\w*|sterili[sz]\w*|"
    r"biocontrol|antifungal|carvacrol|dimethyl dicarbonate)\b",
    re.IGNORECASE,
)
OUT_OF_SCOPE_PATTERN = re.compile(
    r"\b(?:cider gum|spent cider yeast|swine|piglets?|rumen|cattle|livestock|"
    r"silage|ensilage|gut microbiome|intestinal|coffee cider|dragon fruit|"
    r"hylocereus|stingless bee|honey cider|three-leaved|cayratia|anti-aging|"
    r"fibroblast|buckwheat and barley wort|correction:)\b|lead \(pb",
    re.IGNORECASE,
)
PREPRINT_DOI_PREFIXES = ("10.1101/", "10.21203/")

SUBTOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "levures_et_fermentation_alcoolique",
        re.compile(
            r"\b(?:yeasts?|saccharomyces|non[- ]saccharomyces|hanseniaspora|"
            r"metschnikowia|torulaspora|lachancea|starmerella|pichia|kluyveromyces)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "bacteries_lactiques_et_malolactique",
        re.compile(
            r"\b(?:oenococcus|malolactic|lactic acid bacter\w*|lactobac\w*|"
            r"lactiplantibacillus|leuconostoc|pediococcus)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ecologie_microbienne_et_phages",
        re.compile(
            r"\b(?:microbiome|microbiota|microbial (?:community|ecology|succession|"
            r"diversity|dynamics)|phages?|phageome)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "moisissures_et_patuline",
        re.compile(
            r"\b(?:penicillium|paecilomyces|patulin|mycotoxin\w*|blue mo[u]?ld|"
            r"fungal (?:community|decay|disease))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "alicyclobacillus_et_alterations",
        re.compile(
            r"\b(?:alicyclobacillus|guaiacol|zygosaccharomyces|brettanomyces|"
            r"dekker\w*|spoilage)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pathogenes_alimentaires",
        re.compile(
            r"\b(?:escherichia|e\.?\s*coli|salmonella|listeria|cryptosporidium|"
            r"foodborne|food safety|outbreak)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "maitrise_et_inactivation",
        re.compile(
            r"\b(?:inactivat\w*|pasteuri[sz]\w*|sterili[sz]\w*|biocontrol|"
            r"antifungal|pulsed electric|ultraviolet|UV-C|cold plasma|high pressure)\b",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class VerifiedArticle:
    axis: str
    subtopics: tuple[str, ...]
    title: str
    doi: str
    authors: list[str]
    journal: str
    publication_year: int | None
    publication_status: str
    source: str
    source_url: str | None
    license: str | None
    pdf_path: str
    sha256: str
    byte_count: int
    page_count: int
    extracted_text_characters: int
    chunk_count: int
    citation: str
    selection_score: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target", type=int, default=100)
    parser.add_argument("--fermentation-target", type=int, default=FERMENTATION_TARGET)
    parser.add_argument("--contaminant-target", type=int, default=CONTAMINANT_TARGET)
    parser.add_argument("--output-dir", type=Path)
    return parser


def classify_title(title: str) -> set[str]:
    if OUT_OF_SCOPE_PATTERN.search(title):
        return set()
    axes: set[str] = set()
    if BEVERAGE_ANCHOR.search(title) and FERMENTATION_PATTERN.search(title):
        axes.add("microorganismes_des_fermentations")
    if APPLE_CONTAMINANT_ANCHOR.search(title) and CONTAMINANT_PATTERN.search(title):
        axes.add("contaminants_et_alterations")
    return axes


def classify_subtopics(title: str, abstract: str | None) -> tuple[str, ...]:
    text = f"{title} {abstract or ''}"
    matches = [name for name, pattern in SUBTOPIC_PATTERNS if pattern.search(text)]
    return tuple(matches or ["microbiologie_generale"])


def _parse_authors(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(author).strip() for author in value if str(author).strip()]


def _citation(
    authors: list[str],
    publication_year: int | None,
    title: str,
    journal: str,
    doi: str,
) -> str:
    if not authors:
        author_text = "Auteur non renseigné"
    elif len(authors) <= 3:
        author_text = ", ".join(authors)
    else:
        author_text = f"{authors[0]} et al."
    return (
        f"{author_text} ({publication_year or 's. d.'}). {title}. {journal}. https://doi.org/{doi}"
    )


def _provenance(connection: sqlite3.Connection, doi: str) -> dict[str, str | None]:
    row = connection.execute(
        """
        SELECT f.source, COALESCE(f.final_url, f.source_url) AS source_url, f.license
        FROM full_text_assets AS f
        WHERE f.doi = ? COLLATE NOCASE
          AND f.state = 'ingested'
        ORDER BY CASE f.source
            WHEN 'europe_pmc' THEN 0
            WHEN 'hal' THEN 1
            WHEN 'doaj' THEN 2
            WHEN 'openalex' THEN 3
            ELSE 4
        END, f.updated_at DESC
        LIMIT 1
        """,
        (doi,),
    ).fetchone()
    if row is None:
        return {"source": None, "source_url": None, "license": None}
    return {
        "source": str(row["source"]),
        "source_url": str(row["source_url"]) if row["source_url"] else None,
        "license": str(row["license"]) if row["license"] else None,
    }


def _verify_pdf(path: Path) -> tuple[str, int, int, int]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    with resolved.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("file does not start with a PDF signature")
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    with fitz.open(resolved) as document:
        page_count = document.page_count
        text_characters = sum(len(page.get_text("text").strip()) for page in document)
    if page_count < 3:
        raise ValueError("document has fewer than three pages")
    if text_characters < 2_500:
        raise ValueError("document does not contain enough extractable article text")
    return digest, resolved.stat().st_size, page_count, text_characters


def _score(row: sqlite3.Row, subtopics: tuple[str, ...], page_count: int) -> int:
    score = len(subtopics) * 3
    score += min(int(row["chunk_count"]), 20)
    if row["abstract"] and len(str(row["abstract"])) >= 500:
        score += 5
    if 4 <= page_count <= 80:
        score += 3
    if row["publication_year"] and 2000 <= int(row["publication_year"]) <= 2026:
        score += 2
    if str(row["doi"]).casefold().startswith(PREPRINT_DOI_PREFIXES):
        score -= 20
    return score


def _eligible_rows(database: Database) -> list[sqlite3.Row]:
    with closing(database.connect()) as connection:
        return list(
            connection.execute(
                """
                SELECT a.*,
                    (SELECT COUNT(*) FROM chunks AS c WHERE c.article_id = a.id) AS chunk_count
                FROM articles AS a
                WHERE a.doi IS NOT NULL
                  AND length(trim(a.doi)) > 0
                  AND a.journal IS NOT NULL
                  AND length(trim(a.journal)) > 0
                  AND length(trim(a.pdf_path)) > 0
                  AND EXISTS (SELECT 1 FROM chunks AS c WHERE c.article_id = a.id)
                ORDER BY a.title COLLATE NOCASE
                """
            )
        )


def _round_robin_by_subtopic(
    articles: list[VerifiedArticle],
    *,
    limit: int,
    excluded_dois: set[str],
) -> list[VerifiedArticle]:
    buckets: dict[str, list[VerifiedArticle]] = defaultdict(list)
    for article in sorted(
        articles,
        key=lambda item: (
            -item.selection_score,
            -(item.publication_year or 0),
            item.doi,
        ),
    ):
        if article.doi not in excluded_dois:
            buckets[article.subtopics[0]].append(article)
    selected: list[VerifiedArticle] = []
    category_names = sorted(buckets)
    while len(selected) < limit and category_names:
        remaining: list[str] = []
        for name in category_names:
            if buckets[name]:
                selected.append(buckets[name].pop(0))
                if len(selected) >= limit:
                    break
            if buckets[name]:
                remaining.append(name)
        category_names = remaining
    return selected


def build_verified_bibliography(
    settings_path: Path | None,
    *,
    target: int,
    fermentation_target: int,
    contaminant_target: int,
) -> tuple[list[VerifiedArticle], dict[str, Any]]:
    if target < 1 or fermentation_target < 0 or contaminant_target < 0:
        raise ValueError("bibliography targets must be non-negative and target must be positive")
    if fermentation_target + contaminant_target != target:
        raise ValueError("axis targets must add up to the requested total")

    settings = load_settings(settings_path)
    common_settings = settings_for_corpus(settings, CorpusScope.COMMON)
    common_database = Database(common_settings.paths.database_path)
    common_database.initialize()
    metadata_database = common_database

    candidates: dict[str, list[VerifiedArticle]] = {
        "microorganismes_des_fermentations": [],
        "contaminants_et_alterations": [],
    }
    rejected: list[dict[str, str]] = []
    with closing(metadata_database.connect()) as metadata_connection:
        for row in _eligible_rows(common_database):
            title = str(row["title"])
            axes = classify_title(title)
            if not axes:
                continue
            doi = str(row["doi"]).casefold()
            try:
                digest, byte_count, page_count, text_characters = _verify_pdf(
                    Path(str(row["pdf_path"]))
                )
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                rejected.append({"doi": doi, "reason": str(exc)})
                continue
            subtopics = classify_subtopics(title, row["abstract"])
            provenance = _provenance(metadata_connection, doi)
            authors = _parse_authors(str(row["authors"]))
            relative_pdf = Path(str(row["pdf_path"])).resolve()
            try:
                relative_pdf_text = str(relative_pdf.relative_to(settings.paths.data_dir.parent))
            except ValueError:
                relative_pdf_text = str(relative_pdf)
            for axis in axes:
                candidates[axis].append(
                    VerifiedArticle(
                        axis=axis,
                        subtopics=subtopics,
                        title=title,
                        doi=doi,
                        authors=authors,
                        journal=str(row["journal"]),
                        publication_year=(
                            int(row["publication_year"])
                            if row["publication_year"] is not None
                            else None
                        ),
                        publication_status=(
                            "prepublication"
                            if doi.startswith(PREPRINT_DOI_PREFIXES)
                            else "article_publie"
                        ),
                        source=str(provenance["source"] or row["source"]),
                        source_url=provenance["source_url"],
                        license=provenance["license"],
                        pdf_path=relative_pdf_text,
                        sha256=digest,
                        byte_count=byte_count,
                        page_count=page_count,
                        extracted_text_characters=text_characters,
                        chunk_count=int(row["chunk_count"]),
                        citation=_citation(
                            authors,
                            (
                                int(row["publication_year"])
                                if row["publication_year"] is not None
                                else None
                            ),
                            title,
                            str(row["journal"]),
                            doi,
                        ),
                        selection_score=_score(row, subtopics, page_count),
                    )
                )

    fermentation = _round_robin_by_subtopic(
        candidates["microorganismes_des_fermentations"],
        limit=fermentation_target,
        excluded_dois=set(),
    )
    used_dois = {article.doi for article in fermentation}
    contaminants = _round_robin_by_subtopic(
        candidates["contaminants_et_alterations"],
        limit=contaminant_target,
        excluded_dois=used_dois,
    )
    selected = [*fermentation, *contaminants]
    if len(fermentation) != fermentation_target or len(contaminants) != contaminant_target:
        raise RuntimeError(
            "insufficient verified PDFs for the requested balanced bibliography: "
            f"fermentation={len(fermentation)}/{fermentation_target}, "
            f"contaminants={len(contaminants)}/{contaminant_target}"
        )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target": target,
        "selected": len(selected),
        "axis_counts": {
            "microorganismes_des_fermentations": len(fermentation),
            "contaminants_et_alterations": len(contaminants),
        },
        "eligible_counts": {axis: len(values) for axis, values in candidates.items()},
        "rejected_pdf_or_publication_count": len(rejected),
        "rejected_pdf_or_publication": rejected,
        "unique_doi_count": len({article.doi for article in selected}),
        "publication_status_counts": {
            status: sum(article.publication_status == status for article in selected)
            for status in ("article_publie", "prepublication")
        },
        "total_pages": sum(article.page_count for article in selected),
        "total_chunks": sum(article.chunk_count for article in selected),
        "total_pdf_bytes": sum(article.byte_count for article in selected),
        "subtopic_counts": dict(
            sorted(
                {
                    subtopic: sum(subtopic in article.subtopics for article in selected)
                    for subtopic, _ in SUBTOPIC_PATTERNS
                }.items()
            )
        ),
    }
    return selected, report


def _write_outputs(
    output_dir: Path,
    articles: list[VerifiedArticle],
    report: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "microbiology-full-text-bibliography.csv"
    json_path = output_dir / "microbiology-full-text-bibliography.json"
    rows = [asdict(article) for article in articles]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(
            {**row, "subtopics": ";".join(row["subtopics"]), "authors": ";".join(row["authors"])}
            for row in rows
        )
    json_path.write_text(
        json.dumps({"report": report, "articles": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return csv_path, json_path


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = load_settings(arguments.config)
    articles, report = build_verified_bibliography(
        arguments.config,
        target=arguments.target,
        fermentation_target=arguments.fermentation_target,
        contaminant_target=arguments.contaminant_target,
    )
    output_dir = arguments.output_dir or settings.paths.exports_dir
    csv_path, json_path = _write_outputs(output_dir, articles, report)
    print(
        f"selected={report['selected']} unique_dois={report['unique_doi_count']} "
        f"pages={report['total_pages']} chunks={report['total_chunks']}",
        flush=True,
    )
    print(f"csv={csv_path.resolve()}", flush=True)
    print(f"json={json_path.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
