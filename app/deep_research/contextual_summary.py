"""Optional ARGO contextual summariser for the deep-research pipeline.

DRS-009 — adds a bounded per-fragment summary stage that can be skipped entirely
           when no ARGO client is provided or when the stage is disabled.
DRS-010 — filters summaries below the relevance threshold so that a rejected
           summary can never become evidence in the final synthesis.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.corpora import CorpusScope
from app.deep_research.models import ContextualEvidenceGate, ContextualSummaryResult
from app.deep_research.retrieval import DeepResearchFragmentHit

# ---------------------------------------------------------------------------
# Maximum number of fragments sent to ARGO for contextual summarisation.
# Matches the "12 fragments conservés" budget in DEEP_RESEARCH_CONTRACT.md.
# ---------------------------------------------------------------------------
DEFAULT_SUMMARISER_TOP_K: int = 12

# Default relevance threshold below which a summary is considered non-relevant.
DEFAULT_RELEVANCE_THRESHOLD: float = 0.5

# Maximum characters of a single fragment text sent to ARGO.
_MAX_FRAGMENT_CHARS: int = 8_000

# Maximum characters allowed in a returned summary.
_MAX_SUMMARY_CHARS: int = 1_200


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


class SummarisableFragment(BaseModel):
    """A single ranked fragment eligible for contextual summarisation."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["lexical", "vector", "rrf"]
    scope: CorpusScope
    article_id: str
    chunk_id: int = Field(ge=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    score: float
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # The raw text is required here so we can send it to ARGO.
    # It is never written to the checkpoint; only text_sha256 survives.
    text: str = Field(min_length=1)

    @classmethod
    def from_hit_and_text(cls, hit: DeepResearchFragmentHit, text: str) -> SummarisableFragment:
        return cls(
            method=hit.method,
            scope=hit.scope,
            article_id=hit.article_id,
            chunk_id=hit.chunk_id,
            page_start=hit.page_start,
            page_end=hit.page_end,
            score=hit.score,
            text_sha256=hit.text_sha256,
            text=text,
        )


# ---------------------------------------------------------------------------
# ARGO client protocol — kept minimal to stay mockable in tests
# ---------------------------------------------------------------------------

_SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": _MAX_SUMMARY_CHARS},
        "relevance_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["summary", "relevance_score"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Tu es un assistant scientifique. "
    "Tu reçois un fragment de texte scientifique délimité et une question de recherche. "
    "Ton rôle est de résumer factuellement ce fragment en lien avec la question, "
    "puis d'évaluer sa pertinence avec un score de 0.0 à 1.0. "
    "Réponds uniquement avec l'objet JSON demandé. "
    "N'invente aucune donnée, DOI, auteur ou page absents du fragment fourni."
)


class _ArgoGenerationProtocol(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Summariser
# ---------------------------------------------------------------------------


class ContextualSummarizer:
    """Wraps an ARGO client to produce per-fragment contextual summaries.

    When *client* is ``None`` the stage produces an empty list, which allows
    the deep-research pipeline to function without ARGO during the summarisation
    step (DRS-009: "le mode peut fonctionner sans cet étage").
    """

    def __init__(
        self,
        client: _ArgoGenerationProtocol | None,
        *,
        top_k: int = DEFAULT_SUMMARISER_TOP_K,
        relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
        strict_errors: bool = False,
    ) -> None:
        if not 1 <= top_k <= DEFAULT_SUMMARISER_TOP_K:
            raise ValueError(f"top_k must be between 1 and {DEFAULT_SUMMARISER_TOP_K}, got {top_k}")
        if not 0.0 <= relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be between 0.0 and 1.0")
        self.client = client
        self.top_k = top_k
        self.relevance_threshold = relevance_threshold
        self.strict_errors = strict_errors

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize_batch(
        self,
        question: str,
        fragments: list[SummarisableFragment],
    ) -> list[ContextualSummaryResult]:
        """Return contextual summaries for the top-scored fragments.

        * The list is sorted by descending score before truncation so that only
          the best ``top_k`` fragments are dispatched to ARGO.
        * If no ARGO client is configured, an empty list is returned — the
          downstream stages must handle this gracefully.
        * Retrieved fragment text is used only within this method; it is never
          written to disk — only ``text_sha256`` survives in the result.
        """
        if self.client is None:
            return []

        selected = sorted(fragments, key=lambda f: f.score, reverse=True)[: self.top_k]
        results: list[ContextualSummaryResult] = []
        for fragment in selected:
            result = self._summarize_one(question, fragment)
            if result is not None:
                results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _summarize_one(
        self,
        question: str,
        fragment: SummarisableFragment,
    ) -> ContextualSummaryResult | None:
        """Call ARGO for a single fragment; return None on any ARGO error."""
        bounded_text = fragment.text[:_MAX_FRAGMENT_CHARS]
        user_content = (
            f"Question de recherche : {question}\n\n"
            f"--- DÉBUT DONNÉES_NON_FIABLES ---\n"
            f"{bounded_text}\n"
            f"--- FIN DONNÉES_NON_FIABLES ---"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        try:
            response = self.client.chat(
                messages,
                json_schema=_SUMMARY_JSON_SCHEMA,
                temperature=0.0,
                max_output_tokens=512,
            )
            raw = json.loads(response.content)
        except Exception:  # noqa: BLE001 — production keeps the optional stage non-blocking
            if self.strict_errors:
                raise
            return None

        summary = str(raw.get("summary", ""))[:_MAX_SUMMARY_CHARS]
        try:
            score = float(raw["relevance_score"])
        except (KeyError, TypeError, ValueError):
            return None
        score = max(0.0, min(1.0, score))

        return ContextualSummaryResult(
            text_sha256=fragment.text_sha256,
            article_id=fragment.article_id,
            chunk_id=fragment.chunk_id,
            scope=fragment.scope,
            page_start=fragment.page_start,
            page_end=fragment.page_end,
            summary=summary,
            relevance_score=score,
            relevant=score >= self.relevance_threshold,
        )


# ---------------------------------------------------------------------------
# DRS-010 — filter
# ---------------------------------------------------------------------------


def filter_relevant(
    results: list[ContextualSummaryResult],
) -> list[ContextualSummaryResult]:
    """Return only summaries whose ``relevant`` flag is ``True``.

    A rejected summary (``relevant=False``) is permanently excluded from
    evidence: callers must use *this* function's output, not the raw list.
    """
    return [r for r in results if r.relevant]


def build_contextual_evidence(
    results: list[ContextualSummaryResult],
    *,
    threshold: float,
) -> ContextualEvidenceGate:
    """Build the sole downstream contract from summaries passing the active threshold."""

    if not 0.0 <= threshold <= 1.0:
        raise ValueError("contextual relevance threshold must be between 0 and 1")
    accepted = [
        result for result in results if result.relevant and result.relevance_score >= threshold
    ]
    return ContextualEvidenceGate(
        threshold=threshold,
        source_summary_count=len(results),
        rejected_summary_count=len(results) - len(accepted),
        accepted=accepted,
    )
