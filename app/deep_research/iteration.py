"""Persisted, strictly bounded search-iteration contract for deep research."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deep_research.models import ContextualSummaryResult
from app.jobs.contracts import DeepResearchPayload
from app.llm.response_language import (
    output_language_name,
    question_language,
    validate_output_language,
)

MAX_RESEARCH_ITERATIONS = 2


class MissingInformationAssessment(BaseModel):
    """Model decision after inspecting the currently accepted evidence."""

    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    gap_description: str | None = Field(default=None, max_length=1_000)
    follow_up_query: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def require_gap_only_when_insufficient(self) -> MissingInformationAssessment:
        description = self.gap_description.strip() if self.gap_description else None
        query = " ".join(self.follow_up_query.split()) if self.follow_up_query else None
        if self.sufficient and (description or query):
            raise ValueError("sufficient evidence cannot carry a follow-up gap")
        if not self.sufficient and (not description or not query):
            raise ValueError("insufficient evidence requires an explicit gap and follow-up query")
        self.gap_description = description
        self.follow_up_query = query
        return self


class ResearchGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str = Field(pattern=r"^gap-[0-9a-f]{16}$")
    from_iteration: Literal[1] = 1
    description: str = Field(min_length=1, max_length=1_000)
    follow_up_query: str = Field(min_length=2, max_length=2_000)

    @classmethod
    def from_assessment(cls, assessment: MissingInformationAssessment) -> ResearchGap:
        if (
            assessment.sufficient
            or not assessment.gap_description
            or not assessment.follow_up_query
        ):
            raise ValueError("a research gap requires an insufficient assessment")
        digest = hashlib.sha256(
            (f"{assessment.gap_description}\n{assessment.follow_up_query}").encode()
        ).hexdigest()[:16]
        return cls(
            gap_id=f"gap-{digest}",
            description=assessment.gap_description,
            follow_up_query=assessment.follow_up_query,
        )


class ResearchIterationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, le=MAX_RESEARCH_ITERATIONS)
    query: str = Field(min_length=2, max_length=2_000)
    gap: ResearchGap | None = None


ResearchStopReason = Literal[
    "sufficient_evidence",
    "no_valid_gap",
    "gap_assessment_disabled",
    "maximum_iterations",
]


class ResearchLoopCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    original_question: str = Field(min_length=2, max_length=4_000)
    max_iterations: Literal[2] = MAX_RESEARCH_ITERATIONS
    iterations: list[ResearchIterationRecord] = Field(min_length=1, max_length=2)
    stop_reason: ResearchStopReason | None = None

    @model_validator(mode="after")
    def enforce_bounded_explicit_follow_up(self) -> ResearchLoopCheckpoint:
        if [item.index for item in self.iterations] != list(range(1, len(self.iterations) + 1)):
            raise ValueError("research iterations must be contiguous")
        first = self.iterations[0]
        if first.gap is not None or first.query != self.original_question:
            raise ValueError("the first search must use the original question")
        if len(self.iterations) == 2:
            follow_up = self.iterations[1]
            if follow_up.gap is None or follow_up.query != follow_up.gap.follow_up_query:
                raise ValueError("the second search must answer its persisted explicit gap")
            if _normalize_query(follow_up.query) == _normalize_query(first.query):
                raise ValueError("a follow-up search cannot duplicate the original query")
        if self.stop_reason == "maximum_iterations" and len(self.iterations) != 2:
            raise ValueError("maximum-iterations stop requires two searches")
        return self


class ResearchGapAssessor(Protocol):
    def assess(
        self,
        question: str,
        evidence: tuple[ContextualSummaryResult, ...],
    ) -> MissingInformationAssessment | None: ...


class _GenerationClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Any: ...


_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sufficient": {"type": "boolean"},
        "gap_description": {"type": ["string", "null"], "maxLength": 1000},
        "follow_up_query": {"type": ["string", "null"], "maxLength": 2000},
    },
    "required": ["sufficient", "gap_description", "follow_up_query"],
    "additionalProperties": False,
}

_ASSESSMENT_SYSTEM_PROMPT = (
    "Évalue si les résumés de preuves fournis suffisent à répondre factuellement à la question. "
    "S'ils ne suffisent pas, décris une seule lacune précise et propose une seule requête "
    "de recherche qui vise uniquement cette lacune. N'ajoute aucun fait, auteur, DOI ou "
    "résultat. gap_description est visible par l'utilisateur : rédige-le exclusivement dans la "
    "langue de sa question et traduis le contenu des résumés si nécessaire. Réponds avec l'objet "
    "JSON demandé."
)


class ArgoResearchGapAssessor:
    """Ask ARGO for at most one explicit gap using only accepted summaries."""

    def __init__(self, client: _GenerationClient) -> None:
        self.client = client

    def assess(
        self,
        question: str,
        evidence: tuple[ContextualSummaryResult, ...],
    ) -> MissingInformationAssessment | None:
        output_language = question_language(question)
        evidence_payload = [
            {
                "summary": item.summary,
                "scope": item.scope.value,
                "article_id": item.article_id,
                "page_start": item.page_start,
                "page_end": item.page_end,
            }
            for item in evidence[:12]
        ]
        response = self.client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"{_ASSESSMENT_SYSTEM_PROMPT} La langue de sortie obligatoire est "
                        f"{output_language_name(output_language)} ; aucun mélange n'est accepté."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "output_language": output_language,
                            "accepted_evidence": evidence_payload,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            json_schema=_ASSESSMENT_SCHEMA,
            temperature=0.0,
            max_output_tokens=512,
        )
        try:
            assessment = MissingInformationAssessment.model_validate_json(response.content)
            if assessment.gap_description:
                validate_output_language(question, [assessment.gap_description])
            return assessment
        except (RuntimeError, ValueError, TypeError):
            return None


def _normalize_query(query: str) -> str:
    return " ".join(query.casefold().split())


class ResearchLoopStore:
    """Atomic checkpoint ensuring restart cannot create a third search."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, payload: DeepResearchPayload) -> Path:
        return (
            self.root
            / str(payload.conversation_id)
            / str(payload.client_request_id)
            / "research-loop.json"
        )

    def load_or_create(self, payload: DeepResearchPayload) -> ResearchLoopCheckpoint:
        path = self._path(payload)
        if path.is_file():
            return ResearchLoopCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
        checkpoint = ResearchLoopCheckpoint(
            original_question=payload.message,
            iterations=[ResearchIterationRecord(index=1, query=payload.message)],
        )
        self._save(payload, checkpoint)
        return checkpoint

    def plan_follow_up(
        self,
        payload: DeepResearchPayload,
        assessment: MissingInformationAssessment,
    ) -> ResearchLoopCheckpoint:
        current = self.load_or_create(payload)
        if current.stop_reason is not None or len(current.iterations) != 1:
            return current
        gap = ResearchGap.from_assessment(assessment)
        if _normalize_query(gap.follow_up_query) == _normalize_query(payload.message):
            return self.stop(payload, "no_valid_gap")
        updated = current.model_copy(
            update={
                "iterations": [
                    *current.iterations,
                    ResearchIterationRecord(
                        index=2,
                        query=gap.follow_up_query,
                        gap=gap,
                    ),
                ]
            }
        )
        updated = ResearchLoopCheckpoint.model_validate(updated.model_dump())
        self._save(payload, updated)
        return updated

    def stop(
        self,
        payload: DeepResearchPayload,
        reason: ResearchStopReason,
    ) -> ResearchLoopCheckpoint:
        current = self.load_or_create(payload)
        if current.stop_reason is not None:
            return current
        updated = ResearchLoopCheckpoint.model_validate(
            current.model_copy(update={"stop_reason": reason}).model_dump()
        )
        self._save(payload, updated)
        return updated

    def _save(
        self,
        payload: DeepResearchPayload,
        checkpoint: ResearchLoopCheckpoint,
    ) -> None:
        path = self._path(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            checkpoint.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
