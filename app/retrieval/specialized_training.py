"""Versioned training data for a progressively specialized local reranker."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.database.sqlite import Database
from app.evaluation.ciderqa import CiderQASplitDataset

ScientificGrade = Literal["A", "B", "C", "D"]
TrainingSource = Literal["ciderqa", "semantic_a_d"]
_TARGET_BY_GRADE: dict[ScientificGrade, float] = {
    "A": 1.0,
    "B": 0.7,
    "C": 0.2,
    "D": 0.0,
}


class SemanticGradeJudgment(BaseModel):
    """Expert- or protocol-validated A-D decision pointing to one SQLite chunk."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=2, max_length=4_000)
    article_id: str = Field(min_length=1, max_length=200)
    chunk_id: int = Field(gt=0)
    grade: ScientificGrade
    rationale: str = Field(min_length=2, max_length=1_000)


class SpecializedRerankerExample(BaseModel):
    """Pointwise cross-encoder example with immutable source provenance."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    example_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    group_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=2, max_length=4_000)
    document: str = Field(min_length=1, max_length=12_000)
    target_score: float = Field(ge=0.0, le=1.0)
    grade: ScientificGrade
    source: TrainingSource
    article_id: str = Field(min_length=1, max_length=200)
    fragment_id: str = Field(min_length=1, max_length=200)


class SpecializedRerankerDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    examples: list[SpecializedRerankerExample] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_examples(self) -> SpecializedRerankerDataset:
        identifiers = [example.example_id for example in self.examples]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("specialized reranker examples must be unique")
        return self


def _example_id(
    *,
    group_id: str,
    query: str,
    document: str,
    grade: ScientificGrade,
    article_id: str,
    fragment_id: str,
) -> str:
    payload = json.dumps(
        {
            "group_id": group_id,
            "query": query,
            "document": document,
            "grade": grade,
            "article_id": article_id,
            "fragment_id": fragment_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _example(
    *,
    group_id: str,
    query: str,
    document: str,
    grade: ScientificGrade,
    source: TrainingSource,
    article_id: str,
    fragment_id: str,
) -> SpecializedRerankerExample:
    cleaned_query = " ".join(query.split())
    cleaned_document = " ".join(document.split())[:12_000]
    return SpecializedRerankerExample(
        example_id=_example_id(
            group_id=group_id,
            query=cleaned_query,
            document=cleaned_document,
            grade=grade,
            article_id=article_id,
            fragment_id=fragment_id,
        ),
        group_id=group_id,
        query=cleaned_query,
        document=cleaned_document,
        target_score=_TARGET_BY_GRADE[grade],
        grade=grade,
        source=source,
        article_id=article_id,
        fragment_id=fragment_id,
    )


def ciderqa_training_examples(
    dataset: CiderQASplitDataset,
) -> list[SpecializedRerankerExample]:
    """Use only development evidence; validation and final-test labels stay sealed."""

    if dataset.split != "development":
        raise ValueError("reranker training accepts only CiderQA development")
    examples: list[SpecializedRerankerExample] = []
    for question in dataset.questions:
        for evidence in question.reference_evidence:
            examples.append(
                _example(
                    group_id=question.id,
                    query=question.question,
                    document=evidence.excerpt,
                    grade="A",
                    source="ciderqa",
                    article_id=evidence.article_id,
                    fragment_id=evidence.fragment_id,
                )
            )
    return examples


def semantic_grade_training_examples(
    database: Database,
    judgments: Sequence[SemanticGradeJudgment],
) -> list[SpecializedRerankerExample]:
    """Hydrate exact training text from SQLite rather than trusting decision files."""

    chunk_ids = list(dict.fromkeys(judgment.chunk_id for judgment in judgments))
    rows = database.chunk_details_by_ids(chunk_ids)
    examples: list[SpecializedRerankerExample] = []
    seen_decisions: dict[str, SemanticGradeJudgment] = {}
    for judgment in judgments:
        existing = seen_decisions.get(judgment.decision_id)
        if existing is not None and existing != judgment:
            raise ValueError("one semantic decision id has conflicting values")
        if existing is not None:
            continue
        seen_decisions[judgment.decision_id] = judgment
        row = rows.get(judgment.chunk_id)
        if row is None or str(row["article_id"]) != judgment.article_id:
            raise ValueError("semantic A-D decision does not match an authoritative SQLite chunk")
        examples.append(
            _example(
                group_id=judgment.decision_id,
                query=judgment.question,
                document=str(row["text"]),
                grade=judgment.grade,
                source="semantic_a_d",
                article_id=judgment.article_id,
                fragment_id=str(judgment.chunk_id),
            )
        )
    return examples


def build_specialized_reranker_dataset(
    *example_groups: Iterable[SpecializedRerankerExample],
) -> SpecializedRerankerDataset:
    by_id: dict[str, SpecializedRerankerExample] = {}
    for example in (item for group in example_groups for item in group):
        existing = by_id.get(example.example_id)
        if existing is not None and existing != example:
            raise ValueError("one reranker example id has conflicting values")
        by_id[example.example_id] = example
    ordered = sorted(
        by_id.values(),
        key=lambda item: (item.source, item.group_id, item.article_id, item.fragment_id),
    )
    return SpecializedRerankerDataset(examples=ordered)


def load_semantic_grade_judgments(path: str | Path) -> list[SemanticGradeJudgment]:
    judgments: list[SemanticGradeJudgment] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            judgments.append(SemanticGradeJudgment.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid semantic grade judgment at line {line_number}") from exc
    return judgments


def write_specialized_reranker_dataset(
    dataset: SpecializedRerankerDataset,
    destination: str | Path,
) -> Path:
    """Atomically write deterministic JSONL suitable for incremental training."""

    path = Path(destination).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for example in dataset.examples:
                handle.write(example.model_dump_json() + "\n")
        Path(temporary_name).replace(path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path


def load_specialized_reranker_dataset(path: str | Path) -> SpecializedRerankerDataset:
    examples = [
        SpecializedRerankerExample.model_validate_json(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return SpecializedRerankerDataset(examples=examples)
