"""Pure helpers for contextual, source-traceable chatbot orchestration."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from app.corpora import CorpusScope
from app.models.chatbot import ChatbotSource, ChatEvidenceRecord
from app.updates.harvest import CIDER_PILOT_THEMES, assess_cider_relevance
from app.updates.models import BibliographicRecord
from app.updates.vector_index import BibliographicHybridResult

TITLE_TOKEN = re.compile(r"[^a-z0-9]+")
EXTERNAL_PREFIX = "external:"
COMMON_PREFIX = "common:"
ChatInteractionMode = Literal["auto", "research", "conversation"]
ResolvedChatInteractionMode = Literal["research", "conversation"]

RESEARCH_INTENT_PATTERN = re.compile(
    r"\b(recherch|cherche|trouve|identifie)\w*\b|"
    r"\b(nouvell\w*\s+(source|article|publication|etude)\w*|"
    r"autre\w*\s+(source|article|publication|etude)\w*|"
    r"revue\s+de\s+litterature|recherche\s+bibliographique)\b"
)
CONVERSATION_INTENT_PATTERN = re.compile(
    r"\b(reformul|resume|raccourc|developp|detail|precis|clarifi|explique|"
    r"tableau|liste|puce|plan|forme|format|ton|style|tradui|compare|"
    r"cette reponse|ces resultats|ce resultat|ce point|cela|ceci|plus court|"
    r"plus long|autrement)\w*\b"
)


class ChatbotNoSourcesError(RuntimeError):
    """No source-valid abstract is available for a chatbot response."""


def contextualize_retrieval_query(
    message: str,
    history: Sequence[Mapping[str, str]],
) -> str:
    """Add only recent user intent to a bounded retrieval query."""

    cleaned_message = " ".join(message.split())
    previous_questions = [
        " ".join(item.get("content", "").split())
        for item in history
        if item.get("role") == "user" and item.get("content", "").strip()
    ][-2:]
    context = [question for question in previous_questions if question != cleaned_message]
    combined = " ".join([*context, cleaned_message]).strip()
    return combined[-4000:]


def resolve_chat_interaction_mode(
    message: str,
    history: Sequence[Mapping[str, str]],
    requested_mode: ChatInteractionMode,
    *,
    has_reusable_sources: bool,
) -> ResolvedChatInteractionMode:
    """Choose whether to search again or discuss the sources already in context."""

    if requested_mode == "research":
        return "research"
    if requested_mode == "conversation":
        return "conversation" if has_reusable_sources else "research"
    if not has_reusable_sources:
        return "research"

    normalized = _plain_text(message)
    if RESEARCH_INTENT_PATTERN.search(normalized):
        return "research"
    if CONVERSATION_INTENT_PATTERN.search(normalized):
        return "conversation"

    recent_assistant = any(
        item.get("role") == "assistant" and item.get("content", "").strip() for item in history[-2:]
    )
    words = normalized.split()
    conversational_opening = normalized.startswith(
        ("et ", "mais ", "pourquoi ", "comment ", "peux tu ", "pourrais tu ", "qu en est il")
    )
    if recent_assistant and (conversational_opening or len(words) <= 10):
        return "conversation"
    return "research"


def latest_chatbot_sources(messages: Sequence[Mapping[str, Any]]) -> list[ChatbotSource]:
    """Return the latest validated source set persisted with an assistant response."""

    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        response = message.get("response")
        if not isinstance(response, Mapping):
            continue
        raw_sources = response.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            continue
        sources: list[ChatbotSource] = []
        for raw_source in raw_sources[:10]:
            try:
                sources.append(ChatbotSource.model_validate(raw_source))
            except (TypeError, ValueError):
                continue
        if sources:
            return sources
    return []


def chatbot_candidates_from_sources(
    sources: Sequence[ChatbotSource],
) -> list[BibliographicHybridResult]:
    """Rebuild a bounded RAG context from sources persisted with a previous answer."""

    return [
        BibliographicHybridResult(
            rank=index,
            record_id=source.record_id,
            title=source.title,
            abstract=source.snippet,
            authors=source.authors,
            journal=source.journal,
            publication_year=source.publication_year,
            doi=source.doi,
            url=source.url,
            sources=source.providers,
            lexical_rank=None,
            vector_rank=None,
            score=0.0,
        )
        for index, source in enumerate(sources[:10], 1)
        if source.snippet.strip()
    ]


def conversation_context(
    history: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    """Keep a small conversational window; it clarifies intent but is never evidence."""

    bounded: list[dict[str, str]] = []
    for item in history[-10:]:
        role = item.get("role")
        content = " ".join(item.get("content", "").split())
        if role not in {"user", "assistant"} or not content:
            continue
        bounded.append({"role": role, "content": content[:2000]})
    return bounded


def merge_chatbot_candidates(
    local_records: Sequence[BibliographicHybridResult],
    external_records: Sequence[BibliographicRecord],
    *,
    limit: int = 10,
) -> tuple[list[BibliographicHybridResult], int]:
    """Reserve room for qualified live sources without displacing the local RAG."""

    if not 1 <= limit <= 16:
        raise ValueError("chatbot source limit must be between 1 and 16")
    qualified_external = [
        record for record in external_records if record.abstract and _is_cider_relevant(record)
    ]
    chosen: list[BibliographicHybridResult] = []
    seen: set[str] = set()

    local_quota = min(12 if limit > 12 else 6, limit)
    for record in local_records:
        if len(chosen) >= local_quota:
            break
        if _source_key(record.doi, record.title) in seen:
            continue
        chosen.append(record)
        seen.add(_source_key(record.doi, record.title))

    external_added = 0
    external_quota = min(4, limit - len(chosen))
    for record in qualified_external:
        if external_added >= external_quota:
            break
        key = _source_key(record.doi, record.title)
        if key in seen:
            continue
        chosen.append(_external_result(record, rank=len(chosen) + 1))
        seen.add(key)
        external_added += 1

    for record in local_records:
        if len(chosen) >= limit:
            break
        key = _source_key(record.doi, record.title)
        if key in seen:
            continue
        chosen.append(record)
        seen.add(key)

    for record in qualified_external:
        if len(chosen) >= limit or external_added >= 4:
            break
        key = _source_key(record.doi, record.title)
        if key in seen:
            continue
        chosen.append(_external_result(record, rank=len(chosen) + 1))
        seen.add(key)
        external_added += 1

    ranked = [record.model_copy(update={"rank": index}) for index, record in enumerate(chosen, 1)]
    return ranked, external_added


def chatbot_sources(
    records: Sequence[BibliographicHybridResult],
    cited_record_ids: Sequence[str],
) -> list[ChatbotSource]:
    cited = set(cited_record_ids)
    return [
        ChatbotSource(
            record_id=record.record_id,
            origin=(
                "external_api" if record.record_id.startswith(EXTERNAL_PREFIX) else "local_rag"
            ),
            evidence_level="abstract",
            scope=_record_scope(record.record_id),
            title=record.title,
            authors=record.authors,
            doi=record.doi,
            journal=record.journal,
            publication_year=record.publication_year,
            providers=record.sources,
            url=record.url,
            snippet=record.abstract[:800],
        )
        for record in records
        if record.record_id in cited
    ]


def chatbot_sources_from_evidence(
    records: Sequence[ChatEvidenceRecord],
    cited_evidence_ids: Sequence[str],
) -> list[ChatbotSource]:
    """Render only cited records while preserving passage ids for future turns."""

    cited = set(cited_evidence_ids)
    sources: list[ChatbotSource] = []
    for record in records:
        passages = [passage for passage in record.passages if passage.evidence_id in cited]
        if not passages:
            continue
        page_ranges = [
            (
                str(passage.page_start)
                if passage.page_start == passage.page_end
                else f"{passage.page_start}-{passage.page_end}"
            )
            for passage in passages
            if passage.page_start is not None and passage.page_end is not None
        ]
        sources.append(
            ChatbotSource(
                record_id=record.record_id,
                origin=record.origin,
                evidence_level=record.evidence_level,
                scope=record.scope,
                article_id=record.article_id,
                chunk_ids=[
                    passage.chunk_id for passage in passages if passage.chunk_id is not None
                ],
                page_ranges=list(dict.fromkeys(page_ranges)),
                figure_refs=list(
                    dict.fromkeys(
                        passage.figure_label
                        for passage in passages
                        if passage.evidence_kind == "figure" and passage.figure_label is not None
                    )
                ),
                title=record.title,
                authors=record.authors,
                doi=record.doi,
                journal=record.journal,
                publication_year=record.publication_year,
                providers=record.providers,
                url=record.url,
                snippet=" ".join(passage.text for passage in passages)[:800],
            )
        )
    return sources


def _record_scope(record_id: str) -> CorpusScope | None:
    if record_id.startswith(EXTERNAL_PREFIX):
        return None
    return CorpusScope.COMMON


def _source_key(doi: str | None, title: str) -> str:
    if doi:
        return f"doi:{doi.casefold()}"
    return f"title:{TITLE_TOKEN.sub('', title.casefold())}"


def _is_cider_relevant(record: BibliographicRecord) -> bool:
    assessments = [assess_cider_relevance(record, theme) for theme in CIDER_PILOT_THEMES]
    return max(assessments, key=lambda item: item.score).status != "rejected"


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return normalized.encode("ascii", "ignore").decode("ascii")


def _external_result(
    record: BibliographicRecord,
    *,
    rank: int,
) -> BibliographicHybridResult:
    source_slug = TITLE_TOKEN.sub("-", record.source.casefold()).strip("-") or "source"
    return BibliographicHybridResult(
        rank=rank,
        record_id=f"{EXTERNAL_PREFIX}{source_slug}:{record.source_id}",
        title=record.title,
        abstract=record.abstract or "",
        authors=record.authors,
        journal=record.journal,
        publication_year=record.publication_year,
        doi=record.doi,
        url=record.url,
        sources=[record.source],
        lexical_rank=None,
        vector_rank=None,
        score=max(record.relevance_score or 0.0, 0.0),
    )
