"""Conservative PDF metadata and DOI extraction without model inference."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.deduplication import GENERIC_TITLES, normalize_title
from app.ingestion.pdf_extractor import PageText
from app.models.article import ArticleMetadata

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20|21)\d{2})(?!\d)")
ABSTRACT_PATTERN = re.compile(
    r"(?is)\b(?:abstract|résumé)\b\s*[:.-]?\s*(.+?)"
    r"(?=\n\s*(?:introduction|keywords?|mots[- ]clés)\b)"
)

# PDF producers often expose a page number, a section label, or a broken font glyph
# as the document title.  These values must not become an article's visible identity.
_UNRELIABLE_TITLE_KEYS = GENERIC_TITLES | {
    "abstract",
    "conclusion",
    "contents",
    "doi",
    "introduction",
    "review",
    "scan",
    "slide",
    "slide 1",
    "summary",
    "title",
}
_TITLE_PREFIX_PATTERN = re.compile(r"^(?:doi|abstract|resume|résumé|slide)\b", re.IGNORECASE)
_ONLY_IDENTIFIER_PATTERN = re.compile(r"^[\W_]*(?:[A-Za-z]{0,3}\d+[A-Za-z\d._-]*)[\W_]*$")
_HASHED_FILENAME_PATTERN = re.compile(r"^[a-f0-9]{12,64}$", re.IGNORECASE)
_PRODUCER_TITLE_PATTERN = re.compile(
    r"(?i)(?:^|\b)(?:microsoft\s+(?:word|powerpoint)|adobe\s+(?:indesign|acrobat))\b"
)
_SOURCE_FILE_SUFFIX_PATTERN = re.compile(
    r"(?i)\.(?:docx?|pptx?|indd|rtf|hwp|book|fm|pdf)(?:\s|\)|\]|$)"
)
_FRONT_MATTER_BOILERPLATE = re.compile(
    r"(?i)\b(?:copyright|printed in|received|accepted|"
    r"department|university|université|institute|issn|journal|vol\.?\s*\d|"
    r"www\.|https?://|e-?mail|"
    r"contents?|keywords?|mots[- ]clés)\b"
)
_EXPLICIT_PUBLICATION_YEAR_PATTERN = re.compile(
    r"(?i)(?:published|publication|publi[ée]|parution|copyright|©)\D{0,40}?"
    r"(?<!\d)((?:19|20|21)\d{2})(?!\d)"
)
_PROSE_LEAD_PATTERN = re.compile(
    r"(?i)^(?:this|the present|we)\s+(?:article|chapter|document|paper|report|study|work)\b"
)


def _looks_like_title_text(candidate: str) -> bool:
    words = candidate.split()
    if len(candidate) > 500 or len(words) > 60:
        return False
    sentence_endings = len(re.findall(r"[.!?](?=\s+[A-ZÀ-ÖØ-Þ]|$)", candidate))
    if sentence_endings > 1:
        return False
    return not (
        _PROSE_LEAD_PATTERN.match(candidate) and candidate.rstrip().endswith((".", "!", "?"))
    )


def is_unidentifiable_local_title(value: str | None) -> bool:
    """Return whether a value is clearly a label or storage artifact, not a title."""

    candidate = " ".join((value or "").split()).strip()
    title_key = normalize_title(candidate)
    if not candidate or title_key in _UNRELIABLE_TITLE_KEYS:
        return True
    if (
        _TITLE_PREFIX_PATTERN.match(candidate)
        or _ONLY_IDENTIFIER_PATTERN.fullmatch(candidate)
        or _PRODUCER_TITLE_PATTERN.search(candidate)
        or _SOURCE_FILE_SUFFIX_PATTERN.search(candidate)
    ):
        return True
    return bool(_HASHED_FILENAME_PATTERN.fullmatch(candidate))


def is_reliable_local_title(value: str | None) -> bool:
    """Return whether a local PDF title is safe to display without manual review.

    This is intentionally less strict than duplicate matching: short legitimate titles
    are allowed, while labels, identifiers and damaged one-character glyphs are not.
    """

    candidate = " ".join((value or "").split()).strip()
    title_key = normalize_title(candidate)
    if is_unidentifiable_local_title(candidate) or len(candidate) < 8:
        return False
    alpha_characters = sum(character.isalpha() for character in candidate)
    return (
        alpha_characters >= 4 and len(title_key.split()) >= 2 and _looks_like_title_text(candidate)
    )


def _clean_doi(candidate: str) -> str | None:
    value = candidate.strip().rstrip(".,;:")
    while value.endswith(")") and value.count(")") > value.count("("):
        value = value[:-1]
    value = value.rstrip("]}")
    return value.lower() if DOI_PATTERN.fullmatch(value) else None


def extract_doi(texts: Sequence[str]) -> str | None:
    """Return only a DOI literally present in source text."""

    for text in texts:
        for match in DOI_PATTERN.finditer(text or ""):
            doi = _clean_doi(match.group(0))
            if doi:
                return doi
    return None


def _first_meaningful_line(pages: Sequence[PageText]) -> str | None:
    candidates: list[tuple[float, str]] = []

    def add_candidate(candidate: str, *, position: int, bonus: float = 0.0) -> None:
        candidate = " ".join(candidate.split()).strip()
        candidate = re.sub(
            r"(?i)\s+(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th))\s+edition$",
            "",
            candidate,
        )
        if len(candidate) > 500 or not is_reliable_local_title(candidate):
            return
        if _FRONT_MATTER_BOILERPLATE.search(candidate):
            return
        words = candidate.split()
        if len(words) < 4 or len(words) > 60:
            return
        comma_penalty = 4.0 if candidate.count(",") >= 3 else 0.0
        uppercase_penalty = 5.0 if candidate.isupper() else 0.0
        position_penalty = min(position / 1_000.0, 4.0)
        # Prefer a title-sized block instead of the longest prose block.  The
        # previous length bonus could promote an introductory paragraph.
        title_length_score = max(0.0, 6.0 - abs(len(words) - 14) / 3.0)
        score = title_length_score + bonus - comma_penalty - uppercase_penalty - position_penalty
        candidates.append((score, candidate))

    for page in pages[:2]:
        front_matter = re.split(
            r"(?im)^\s*(?:abstract|résumé|introduction|keywords?|mots[- ]clés)\s*$",
            page.text,
            maxsplit=1,
        )[0]
        blocks = re.split(r"\n\s*\n", front_matter)
        position = 0
        for block in blocks:
            position += len(block) + 1
            if not block.strip() or position > 5_000:
                break
            lines = [" ".join(line.split()).strip() for line in block.splitlines()]
            lines = [line for line in lines if line]
            continuation_bonus = (
                3.0 if len(lines) > 1 and any(len(line.split()) <= 3 for line in lines[1:]) else 0.0
            )
            add_candidate(" ".join(lines), position=position, bonus=continuation_bonus)
            line_position = position - len(block)
            for line in lines:
                add_candidate(line, position=line_position, bonus=2.0)
                line_position += len(line) + 1
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _filename_title_candidate(pdf_path: Path) -> str | None:
    """Use a descriptive local filename only after PDF text and metadata failed."""

    stem = pdf_path.stem
    # Imported files commonly use ``<sha256>-<original name>``.  A bare content
    # hash is not a human-readable identity and must lead to the local-file fallback.
    prefix, separator, remainder = stem.partition("-")
    if separator and _HASHED_FILENAME_PATTERN.fullmatch(prefix):
        stem = remainder
    if _HASHED_FILENAME_PATTERN.fullmatch(stem):
        return None
    candidate = " ".join(re.sub(r"[_-]+", " ", stem).split())
    return candidate if is_reliable_local_title(candidate) else None


def _extract_authors(raw: str | None) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace("\n", " ").strip()
    parts = re.split(r"\s*;\s*|\s+and\s+|\s+et\s+", normalized, flags=re.I)
    authors: list[str] = []
    seen: set[str] = set()
    for part in parts:
        author = " ".join(part.split()).strip()
        identity = author.casefold()
        if author and identity not in seen:
            authors.append(author)
            seen.add(identity)
    return authors


def _first_plausible_year(texts: Sequence[str], *, latest_year: int) -> int | None:
    """Return a literal publication-year candidate, never a future number."""

    for text in texts:
        for match in YEAR_PATTERN.finditer(text or ""):
            year = int(match.group(1))
            if year <= latest_year:
                return year
    return None


def propose_local_publication_year(
    pages: Sequence[PageText], *, latest_year: int | None = None
) -> tuple[int, str] | None:
    """Propose a local year only when its bibliographic evidence is unambiguous.

    A PDF can contain thousands of reference years.  Unlike the lightweight
    ingest fallback, this audit helper never selects the first numeric match.
    """

    current_year = latest_year if latest_year is not None else datetime.now(UTC).year
    front_matter = "\n".join(page.text for page in pages[:3])
    explicit_years = {
        int(match.group(1))
        for match in _EXPLICIT_PUBLICATION_YEAR_PATTERN.finditer(front_matter)
        if int(match.group(1)) <= current_year
    }
    if len(explicit_years) == 1:
        return next(iter(explicit_years)), "pdf_explicit_publication_year"

    # A single non-future year near the front page is useful for manual review,
    # but is deliberately not high-confidence enough for automatic application.
    years = {
        int(match.group(1))
        for match in YEAR_PATTERN.finditer(front_matter)
        if int(match.group(1)) <= current_year
    }
    if len(years) == 1:
        return next(iter(years)), "pdf_single_front_matter_year_review"
    return None


def _detect_language(text: str) -> str | None:
    words = set(re.findall(r"[a-zà-ÿ]+", text.lower()))
    if not words:
        return None
    french = len(words & {"le", "la", "les", "des", "une", "dans", "résultats", "étude"})
    english = len(words & {"the", "and", "of", "in", "results", "study", "this", "with"})
    if french == english == 0:
        return None
    return "fr" if french > english else "en"


def extract_metadata(
    *,
    pdf_path: Path,
    document_metadata: Mapping[str, str],
    pages: Sequence[PageText],
    scan_pages: int = 3,
) -> ArticleMetadata:
    source_pages = pages[:scan_pages]
    source_text = "\n".join(page.text for page in source_pages)
    raw_title = (document_metadata.get("title") or "").strip()
    title = (
        raw_title
        if is_reliable_local_title(raw_title)
        else _first_meaningful_line(source_pages)
        or _filename_title_candidate(pdf_path)
        or "fichier local"
    )

    preferred_doi_sources = [
        document_metadata.get("doi", ""),
        document_metadata.get("subject", ""),
        document_metadata.get("keywords", ""),
    ]
    doi_sources = preferred_doi_sources + [
        value
        for value in document_metadata.values()
        if isinstance(value, str) and value not in preferred_doi_sources
    ]
    doi_sources.append(source_text)
    doi = extract_doi(doi_sources)

    abstract_match = ABSTRACT_PATTERN.search(source_text)
    abstract = " ".join(abstract_match.group(1).split()) if abstract_match else None

    year = _first_plausible_year(
        (
            pdf_path.stem,
            source_text,
            document_metadata.get("creationDate", ""),
            document_metadata.get("modDate", ""),
        ),
        latest_year=datetime.now(UTC).year,
    )

    return ArticleMetadata(
        doi=doi,
        title=" ".join(title.split())[:500],
        abstract=abstract,
        authors=_extract_authors(document_metadata.get("author")),
        journal=(document_metadata.get("journal") or None),
        publication_year=year,
        language=_detect_language(source_text),
        pdf_path=pdf_path.resolve(),
    )
