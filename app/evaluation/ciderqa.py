"""Strict, split-aware CiderQA v1 dataset contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CiderQASplit = Literal["development", "validation", "final_test"]
CiderQAPurpose = Literal["development", "validation", "final_test"]
CiderQALanguage = Literal["fr", "en"]
CiderQATask = Literal[
    "direct", "comparison", "multi_article", "contradiction", "abstention", "follow_up"
]
EvidenceKind = Literal["abstract", "body", "table", "figure"]


class CiderQAEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^evidence-[a-z0-9][a-z0-9-]{2,79}$")
    notice_id: str = Field(min_length=1, max_length=200)
    article_id: str = Field(min_length=1, max_length=200)
    fragment_id: str = Field(min_length=1, max_length=200)
    article_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: EvidenceKind
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=4000)

    @field_validator("notice_id", "article_id", "fragment_id", "excerpt")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("CiderQA text fields cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def ordered_pages(self) -> CiderQAEvidence:
        if self.page_end < self.page_start:
            raise ValueError("evidence page_end must not precede page_start")
        return self


class CiderQAQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    id: str = Field(pattern=r"^ciderqa-[a-z0-9][a-z0-9-]{2,79}$")
    family_id: str = Field(pattern=r"^family-[a-z0-9][a-z0-9-]{2,79}$")
    split: CiderQASplit
    language: CiderQALanguage
    task: CiderQATask
    question: str = Field(min_length=1, max_length=2000)
    conversation_context: list[str] = Field(default_factory=list, max_length=6)
    answerable: bool
    expected_answer: str | None = Field(default=None, max_length=8000)
    expected_claims: list[str] = Field(default_factory=list, max_length=20)
    reference_evidence: list[CiderQAEvidence] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("CiderQA question cannot be blank")
        return cleaned

    @field_validator("conversation_context", "expected_claims")
    @classmethod
    def clean_unique_texts(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("CiderQA lists cannot contain blank text")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("CiderQA lists cannot contain duplicates")
        return cleaned

    @model_validator(mode="after")
    def coherent_reference(self) -> CiderQAQuestion:
        expected = self.expected_answer.strip() if self.expected_answer else None
        if self.answerable:
            if not expected or not self.expected_claims or not self.reference_evidence:
                raise ValueError("answerable questions require answer, claims, and evidence")
            if self.task == "abstention":
                raise ValueError("answerable questions cannot use the abstention task")
        elif expected or self.expected_claims or self.reference_evidence:
            raise ValueError("unanswerable questions cannot carry answer labels or evidence")
        elif self.task != "abstention":
            raise ValueError("unanswerable questions must use the abstention task")
        if self.task == "follow_up" and not self.conversation_context:
            raise ValueError("follow-up questions require versioned conversation context")
        if self.task != "follow_up" and self.conversation_context:
            raise ValueError("conversation context is reserved for follow-up questions")
        evidence_ids = [evidence.id for evidence in self.reference_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("reference evidence identifiers must be unique")
        self.expected_answer = expected
        return self


class CiderQASplitDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    split: CiderQASplit
    questions: list[CiderQAQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_split_and_ids(self) -> CiderQASplitDataset:
        if any(question.split != self.split for question in self.questions):
            raise ValueError("every question must match its enclosing split")
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question identifiers must be unique within a split")
        return self


class CiderQASplitFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    question_count: int = Field(ge=1)


class CiderQAManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    dataset_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    development: CiderQASplitFile
    validation: CiderQASplitFile
    final_test: CiderQASplitFile

    def split_file(self, split: CiderQASplit) -> CiderQASplitFile:
        return getattr(self, split)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_ciderqa_split(path: str | Path) -> CiderQASplitDataset:
    source = Path(path)
    return CiderQASplitDataset.model_validate_json(source.read_text(encoding="utf-8"))


def load_ciderqa_manifest(path: str | Path) -> CiderQAManifest:
    source = Path(path)
    return CiderQAManifest.model_validate_json(source.read_text(encoding="utf-8"))


def _resolved_split_path(manifest_path: Path, split_file: CiderQASplitFile) -> Path:
    root = manifest_path.resolve().parent
    resolved = (root / split_file.path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("CiderQA split path escapes the manifest directory") from exc
    return resolved


def load_split_for_purpose(
    manifest_path: str | Path,
    split: CiderQASplit,
    *,
    purpose: CiderQAPurpose,
) -> CiderQASplitDataset:
    if split == "final_test" and purpose != "final_test":
        raise PermissionError("final-test labels are sealed outside a final-test execution")
    source = Path(manifest_path)
    manifest = load_ciderqa_manifest(source)
    split_file = manifest.split_file(split)
    split_path = _resolved_split_path(source, split_file)
    observed_hash = file_sha256(split_path)
    if observed_hash != split_file.sha256:
        raise ValueError(f"CiderQA {split} hash does not match its frozen manifest")
    dataset = load_ciderqa_split(split_path)
    if dataset.split != split or len(dataset.questions) != split_file.question_count:
        raise ValueError(f"CiderQA {split} metadata does not match its manifest")
    return dataset


def validate_ciderqa_manifest(path: str | Path) -> dict[CiderQASplit, CiderQASplitDataset]:
    source = Path(path)
    datasets = {
        split: load_split_for_purpose(source, split, purpose="final_test")
        for split in ("development", "validation", "final_test")
    }
    id_splits: dict[str, CiderQASplit] = {}
    family_splits: dict[str, CiderQASplit] = {}
    for split, dataset in datasets.items():
        for question in dataset.questions:
            if question.id in id_splits:
                raise ValueError("a CiderQA question appears in multiple splits")
            if question.family_id in family_splits and family_splits[question.family_id] != split:
                raise ValueError("a CiderQA question family crosses split boundaries")
            id_splits[question.id] = split
            family_splits[question.family_id] = split
    return datasets
