"""Section-aware, sentence-aware chunking that retains exact page bounds."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.ingestion.pdf_extractor import PageText
from app.models.chunk import Chunk

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ0-9])")
SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Abstract", re.compile(r"^(?:abstract|résumé)$", re.I)),
    ("Introduction", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?introduction$", re.I)),
    (
        "Materials and methods",
        re.compile(
            r"^(?:\d+(?:\.\d+)*\s+)?(?:materials?\s+and\s+methods?|methods?|matériels?\s+et\s+méthodes?)$",
            re.I,
        ),
    ),
    ("Results", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:results?|résultats?)$", re.I)),
    ("Discussion", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?discussion$", re.I)),
    ("Conclusion", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?conclusions?$", re.I)),
    (
        "Supplementary information",
        re.compile(r"^(?:supplementary\s+(?:information|materials?)|annexes?)$", re.I),
    ),
)


def estimate_tokens(text: str) -> int:
    return len(TOKEN_PATTERN.findall(text))


class TokenBudget(Protocol):
    def count(self, text: str) -> int: ...

    def split(self, text: str, max_tokens: int) -> Iterable[str]: ...


class RegexTokenBudget:
    """Lightweight fallback used by isolated unit tests, never by PDF ingestion."""

    def count(self, text: str) -> int:
        return estimate_tokens(text)

    def split(self, text: str, max_tokens: int) -> Iterator[str]:
        yield from _split_long_sentence(text, max_tokens)


@dataclass(slots=True)
class _Unit:
    page: int
    section: str
    text: str
    tokens: int


def _heading(line: str) -> str | None:
    normalized = " ".join(line.strip().rstrip(":.").split())
    if len(normalized) > 80:
        return None
    for section, pattern in SECTION_PATTERNS:
        if pattern.fullmatch(normalized):
            return section
    return None


def _split_long_sentence(text: str, max_tokens: int) -> Iterable[str]:
    matches = list(TOKEN_PATTERN.finditer(text))
    if len(matches) <= max_tokens:
        yield text
        return

    # Cutting is unavoidable only for a single sentence larger than max_tokens. Use the same
    # token definition for the limit and the split: word-based approximations can exceed the
    # configured bound on equations, references, or punctuation-heavy table text.
    start = 0
    while start < len(matches):
        end = min(start + max_tokens, len(matches))
        if end < len(matches):
            # Prefer a real whitespace boundary in the latter half of the window. If a compact
            # expression has none, the hard token boundary is safer than emitting an oversized
            # chunk; both resulting parts remain exact substrings of the normalized page text.
            earliest_natural_boundary = start + max(1, max_tokens // 2)
            for boundary in range(end, earliest_natural_boundary - 1, -1):
                gap = text[matches[boundary - 1].end() : matches[boundary].start()]
                if any(character.isspace() for character in gap):
                    end = boundary
                    break
        yield text[matches[start].start() : matches[end - 1].end()].strip()
        start = end


class ScientificChunker:
    def __init__(
        self,
        *,
        target_tokens: int = 500,
        max_tokens: int = 750,
        overlap_tokens: int = 80,
        token_budget: TokenBudget | None = None,
    ) -> None:
        if not 0 <= overlap_tokens < target_tokens <= max_tokens:
            raise ValueError("expected 0 <= overlap < target <= max")
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.token_budget = token_budget or RegexTokenBudget()

    def _measure(self, units: Sequence[_Unit]) -> int:
        return self.token_budget.count(" ".join(unit.text for unit in units))

    def _units(self, pages: Sequence[PageText]) -> list[_Unit]:
        units: list[_Unit] = []
        current_section = "Other"
        for page in pages:
            paragraphs: list[tuple[str, str]] = []
            buffer: list[str] = []
            for raw_line in page.text.splitlines():
                line = " ".join(raw_line.split())
                if not line:
                    if buffer:
                        paragraphs.append((current_section, " ".join(buffer)))
                        buffer = []
                    continue
                detected = _heading(line)
                if detected:
                    if buffer:
                        paragraphs.append((current_section, " ".join(buffer)))
                        buffer = []
                    current_section = detected
                    continue
                buffer.append(line)
            if buffer:
                paragraphs.append((current_section, " ".join(buffer)))

            for paragraph_section, paragraph in paragraphs:
                sentences = SENTENCE_BOUNDARY.split(paragraph)
                for sentence in sentences:
                    cleaned = sentence.strip()
                    if not cleaned:
                        continue
                    for part in self.token_budget.split(cleaned, self.max_tokens):
                        units.append(
                            _Unit(
                                page=page.page_number,
                                section=paragraph_section,
                                text=part,
                                tokens=self.token_budget.count(part),
                            )
                        )
        return units

    def chunk(self, pages: Sequence[PageText]) -> list[Chunk]:
        units = self._units(pages)
        chunks: list[Chunk] = []
        current: list[_Unit] = []
        token_count = 0
        has_new_content = False

        def flush(*, keep_overlap: bool) -> None:
            nonlocal current, token_count, has_new_content
            if current and has_new_content:
                text = " ".join(unit.text for unit in current).strip()
                chunks.append(
                    Chunk(
                        section=current[0].section,
                        page_start=min(unit.page for unit in current),
                        page_end=max(unit.page for unit in current),
                        chunk_index=len(chunks),
                        text=text,
                        token_count=self.token_budget.count(text),
                    )
                )
            if keep_overlap and current and self.overlap_tokens:
                tail: list[_Unit] = []
                for unit in reversed(current):
                    candidate = [unit, *tail]
                    if self._measure(candidate) > self.overlap_tokens:
                        break
                    tail = candidate
                current = tail
                token_count = self._measure(tail) if tail else 0
            else:
                current = []
                token_count = 0
            has_new_content = False

        for unit in units:
            boundary = bool(
                current
                and (unit.section != current[-1].section or unit.page - current[-1].page > 1)
            )
            if boundary:
                flush(keep_overlap=False)
            elif current and self._measure([*current, unit]) > self.max_tokens:
                flush(keep_overlap=True)
                if current and self._measure([*current, unit]) > self.max_tokens:
                    current = []
                    token_count = 0

            current.append(unit)
            token_count = self._measure(current)
            if token_count > self.max_tokens:
                raise ValueError("chunk exceeds the configured exact token budget")
            has_new_content = True

            if token_count >= self.target_tokens:
                flush(keep_overlap=True)

        flush(keep_overlap=False)
        return chunks
