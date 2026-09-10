"""Select verbatim evidence without treating similarity as scientific redundancy."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.models.chatbot import ChatEvidenceRecord


def focused_excerpt(text: str, question: str, limit: int) -> str:
    """Return a contiguous original window around the most discriminating sentences."""
    if len(text) <= limit:
        return text
    terms = {word.casefold() for word in re.findall(r"\w{4,}", question)}
    starts = [0, *(match.end() for match in re.finditer(r"[.!?]\s+|\n", text))]
    windows = [(start, text[start : start + limit]) for start in starts if start < len(text)]
    start, _ = max(
        windows,
        key=lambda item: (
            len(terms.intersection(re.findall(r"\w{4,}", item[1].casefold()))),
            -item[0],
        ),
    )
    start = max(0, min(start, len(text) - limit))
    return text[start : start + limit]


def distinct_evidence(
    records: Sequence[ChatEvidenceRecord], question: str, *, passage_limit: int = 2_400
) -> tuple[list[ChatEvidenceRecord], int]:
    """Remove only exact sentence-subset duplicates within one persisted article.

    Different studies, negations, numerical results and conditions remain distinct.
    Deduplication precedes excerpt fitting, so a truncated tail cannot hide novelty.
    """
    selected: list[ChatEvidenceRecord] = []
    removed = 0
    for record in records:
        known: set[str] = set()
        passages = []
        for passage in record.passages:
            sentences = {
                " ".join(sentence.split())
                for sentence in re.split(r"(?<=[.!?])\s+|\n+", passage.text)
                if sentence.strip()
            }
            if sentences and sentences <= known:
                removed += 1
                continue
            known.update(sentences)
            passages.append(
                passage.model_copy(
                    update={"text": focused_excerpt(passage.text, question, passage_limit)}
                )
            )
        if passages:
            selected.append(record.model_copy(update={"passages": passages}))
    return selected, removed
