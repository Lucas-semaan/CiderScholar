"""Safe natural-language lexical retrieval over SQLite FTS5 and BM25."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.corpora import CorpusScope
from app.database.sqlite import Database, DatabaseReadSession

QueryMode = Literal["any", "all", "phrase"]
TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)

# Function words only. Scientific nouns such as "effect", "study", and "result" are retained.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "après",
        "are",
        "as",
        "at",
        "au",
        "aux",
        "avant",
        "avec",
        "be",
        "by",
        "can",
        "ce",
        "ces",
        "cet",
        "cette",
        "comme",
        "comment",
        "could",
        "de",
        "des",
        "did",
        "do",
        "does",
        "dont",
        "du",
        "dans",
        "elle",
        "elles",
        "en",
        "entre",
        "et",
        "for",
        "from",
        "has",
        "have",
        "how",
        "il",
        "ils",
        "in",
        "is",
        "it",
        "its",
        "la",
        "le",
        "leur",
        "leurs",
        "les",
        "ne",
        "nous",
        "of",
        "on",
        "or",
        "ou",
        "pas",
        "par",
        "pendant",
        "plus",
        "pour",
        "qui",
        "que",
        "quel",
        "quelle",
        "quels",
        "quelles",
        "sur",
        "sa",
        "sans",
        "se",
        "ses",
        "son",
        "sont",
        "that",
        "the",
        "these",
        "this",
        "those",
        "to",
        "un",
        "une",
        "what",
        "would",
        "with",
        "y",
        "à",
    }
)


class PreparedLexicalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_query: str
    normalized_query: str
    terms: list[str]
    fts5_expression: str
    mode: QueryMode


class LexicalSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    chunk_id: int = Field(gt=0)
    article_id: str
    article_title: str
    publication_year: int | None
    section: str | None
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    text: str
    bm25_score: float
    relevance_score: float = Field(ge=0.0)
    scope: CorpusScope = CorpusScope.COMMON


class LexicalSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: PreparedLexicalQuery
    results: list[LexicalSearchResult]
    duration_seconds: float = Field(ge=0.0)


class LexicalReadSession:
    """Reuse one bounded SQLite read connection across lexical query variants."""

    def __init__(self, service: LexicalSearchService) -> None:
        self._service = service
        self._session: DatabaseReadSession | None = None

    def __enter__(self) -> LexicalReadSession:
        cache_size_kib, mmap_size_bytes = self._service._read_session_limits()
        self._session = self._service.database.read_session(
            cache_size_kib=cache_size_kib,
            mmap_size_bytes=mmap_size_bytes,
        )
        self._session.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        if self._session is not None:
            self._session.__exit__(*args)
            self._session = None

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        mode: QueryMode = "any",
        prefix_matching: bool | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> LexicalSearchResponse:
        if self._session is None:
            raise RuntimeError("lexical read session is not active")
        return self._service._search(
            query,
            limit=limit,
            mode=mode,
            prefix_matching=prefix_matching,
            article_ids=article_ids,
            sections=sections,
            database=self._session,
        )


class LexicalQueryBuilder:
    def __init__(self, settings: Settings) -> None:
        self.config = settings.retrieval

    def build(
        self,
        query: str,
        mode: QueryMode = "any",
        *,
        prefix_matching: bool | None = None,
    ) -> PreparedLexicalQuery:
        original = query.strip()
        if mode not in {"any", "all", "phrase"}:
            raise ValueError(f"unsupported lexical query mode: {mode}")
        if len(original) > self.config.lexical_max_query_characters:
            raise ValueError("lexical query exceeds the configured character limit")
        normalized = unicodedata.normalize("NFKC", original).casefold()
        raw_terms = TOKEN_PATTERN.findall(normalized)
        if mode == "phrase":
            filtered = raw_terms
        else:
            filtered = [term for term in raw_terms if term not in STOPWORDS]
        terms: list[str] = []
        for term in filtered:
            if (
                mode != "phrase"
                and len(term) < self.config.lexical_min_token_length
                and not term.isdigit()
            ):
                continue
            if term not in terms:
                terms.append(term)
            if len(terms) >= self.config.lexical_max_terms:
                break

        use_prefix_matching = (
            self.config.lexical_prefix_matching if prefix_matching is None else prefix_matching
        )
        if not terms:
            expression = ""
        elif mode == "phrase":
            expression = f'"{" ".join(terms)}"'
        else:
            operator = " OR " if mode == "any" else " AND "
            encoded_terms: list[str] = []
            for term in terms:
                encoded = f'"{term}"'
                if (
                    use_prefix_matching
                    and len(term) >= self.config.lexical_prefix_min_length
                    and not term.isdigit()
                ):
                    encoded += "*"
                encoded_terms.append(encoded)
            expression = operator.join(encoded_terms)

        return PreparedLexicalQuery(
            original_query=original,
            normalized_query=normalized,
            terms=terms,
            fts5_expression=expression,
            mode=mode,
        )


class LexicalSearchService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.query_builder = LexicalQueryBuilder(settings)

    def _read_session_limits(self) -> tuple[int, int]:
        """Keep per-retrieval SQLite memory bounded for the configured device profile."""

        if self.settings.memory.profile == "16gb":
            return (64 * 1024, 256 * 1024 * 1024)
        if self.settings.memory.profile == "8gb":
            return (32 * 1024, 128 * 1024 * 1024)
        return (16 * 1024, 64 * 1024 * 1024)

    def read_session(self) -> LexicalReadSession:
        """Create an explicitly closed session for multiple retrieval variants."""

        return LexicalReadSession(self)

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        mode: QueryMode = "any",
        prefix_matching: bool | None = None,
        article_ids: Sequence[str] | None = None,
        sections: Sequence[str] | None = None,
    ) -> LexicalSearchResponse:
        return self._search(
            query,
            limit=limit,
            mode=mode,
            prefix_matching=prefix_matching,
            article_ids=article_ids,
            sections=sections,
            database=self.database,
        )

    def _search(
        self,
        query: str,
        *,
        limit: int | None,
        mode: QueryMode,
        prefix_matching: bool | None,
        article_ids: Sequence[str] | None,
        sections: Sequence[str] | None,
        database: Database | DatabaseReadSession,
    ) -> LexicalSearchResponse:
        started = perf_counter()
        prepared = self.query_builder.build(query, mode, prefix_matching=prefix_matching)
        search_limit = self.settings.retrieval.lexical_default_limit if limit is None else limit
        if search_limit <= 0 or search_limit > 1000:
            raise ValueError("lexical search limit must be between 1 and 1000")
        if not prepared.fts5_expression:
            return LexicalSearchResponse(
                query=prepared,
                results=[],
                duration_seconds=perf_counter() - started,
            )

        rows = database.lexical_search(
            prepared.fts5_expression,
            search_limit,
            article_ids=list(dict.fromkeys(article_ids)) if article_ids is not None else None,
            sections=list(dict.fromkeys(sections)) if sections is not None else None,
            section_weight=self.settings.retrieval.lexical_section_weight,
            text_weight=self.settings.retrieval.lexical_text_weight,
        )
        results = [
            LexicalSearchResult(
                rank=rank,
                chunk_id=int(row["id"]),
                article_id=str(row["article_id"]),
                article_title=str(row["article_title"]),
                publication_year=(
                    int(row["publication_year"]) if row["publication_year"] is not None else None
                ),
                section=row["section"],
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
                text=str(row["text"]),
                bm25_score=float(row["lexical_score"]),
                relevance_score=max(0.0, -float(row["lexical_score"])),
            )
            for rank, row in enumerate(rows, start=1)
        ]
        return LexicalSearchResponse(
            query=prepared,
            results=results,
            duration_seconds=perf_counter() - started,
        )
