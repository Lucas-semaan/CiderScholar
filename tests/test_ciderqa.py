from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.ciderqa import (
    CiderQAQuestion,
    file_sha256,
    load_split_for_purpose,
    validate_ciderqa_manifest,
)


def _question(
    identifier: str,
    split: str,
    *,
    family: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": identifier,
        "family_id": family or f"family-{identifier.removeprefix('ciderqa-')}",
        "split": split,
        "language": "fr",
        "task": "direct",
        "question": "Quel résultat est observé ?",
        "conversation_context": [],
        "answerable": True,
        "expected_answer": "Le résultat diminue.",
        "expected_claims": ["Le résultat diminue."],
        "reference_evidence": [
            {
                "id": "evidence-result",
                "notice_id": "notice-1",
                "article_id": "article-1",
                "fragment_id": "fragment-1",
                "article_sha256": "a" * 64,
                "kind": "body",
                "page_start": 4,
                "page_end": 4,
                "excerpt": "Le résultat diminue dans la condition étudiée.",
            }
        ],
    }


def _write_manifest(tmp_path: Path, *, shared_family: bool = False) -> Path:
    entries: dict[str, dict[str, object]] = {}
    for split in ("development", "validation", "final_test"):
        identifier = f"ciderqa-{split.replace('_', '-')}"
        family = "family-shared" if shared_family else None
        split_path = tmp_path / f"{split}.json"
        split_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "split": split,
                    "questions": [_question(identifier, split, family=family)],
                }
            ),
            encoding="utf-8",
        )
        entries[split] = {
            "path": split_path.name,
            "sha256": file_sha256(split_path),
            "question_count": 1,
        }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "dataset_version": "1.0.0", **entries}),
        encoding="utf-8",
    )
    return manifest_path


def test_ciderqa_question_is_strict_and_requires_page_evidence() -> None:
    valid = _question("ciderqa-valid", "development")
    assert CiderQAQuestion.model_validate(valid).reference_evidence[0].page_start == 4

    with pytest.raises(ValidationError, match="extra"):
        CiderQAQuestion.model_validate({**valid, "expected_concepts": ["label leak"]})
    with pytest.raises(ValidationError, match="require answer"):
        CiderQAQuestion.model_validate({**valid, "expected_answer": None})
    with pytest.raises(ValidationError, match="cannot carry"):
        CiderQAQuestion.model_validate(
            {
                **valid,
                "task": "abstention",
                "answerable": False,
            }
        )


def test_final_test_is_hash_frozen_and_sealed_from_tuning(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)

    with pytest.raises(PermissionError, match="sealed"):
        load_split_for_purpose(manifest_path, "final_test", purpose="validation")
    final_test = load_split_for_purpose(manifest_path, "final_test", purpose="final_test")
    assert final_test.questions[0].split == "final_test"

    (tmp_path / "final_test.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen manifest"):
        load_split_for_purpose(manifest_path, "final_test", purpose="final_test")


def test_question_families_cannot_cross_split_boundaries(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, shared_family=True)

    with pytest.raises(ValueError, match="family crosses"):
        validate_ciderqa_manifest(manifest_path)
