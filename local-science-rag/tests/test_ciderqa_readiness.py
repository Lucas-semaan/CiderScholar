from __future__ import annotations

from app.evaluation.ciderqa import CiderQAQuestion, CiderQASplitDataset
from app.evaluation.ciderqa_readiness import assess_ciderqa_readiness


def _question(index: int, split: str) -> CiderQAQuestion:
    unanswerable = index < 15
    task = "abstention" if unanswerable else ("multi_article" if index < 35 else "direct")
    evidence = []
    if not unanswerable:
        evidence = [
            {
                "id": f"evidence-item-{index:03d}",
                "notice_id": f"notice-{index}",
                "article_id": f"article-{index}",
                "fragment_id": f"fragment-{index}",
                "article_sha256": f"{index:064x}"[-64:],
                "kind": "body" if index < 40 else "abstract",
                "page_start": 1,
                "page_end": 1,
                "excerpt": "Extrait scientifique validé.",
            }
        ]
    return CiderQAQuestion.model_validate(
        {
            "schema_version": 1,
            "id": f"ciderqa-item-{index:03d}",
            "family_id": f"family-item-{index:03d}",
            "split": split,
            "language": "fr" if index < 50 else "en",
            "task": task,
            "question": f"Question {index} ?",
            "answerable": not unanswerable,
            "expected_answer": None if unanswerable else "Réponse validée.",
            "expected_claims": [] if unanswerable else ["Affirmation validée."],
            "reference_evidence": evidence,
        }
    )


def test_ciderqa_readiness_enforces_public_protocol_quotas() -> None:
    development = [_question(index, "development") for index in range(50)]
    validation = [_question(index, "validation") for index in range(50, 80)]
    final_test = [_question(index, "final_test") for index in range(80, 100)]
    datasets = {
        "development": CiderQASplitDataset(
            schema_version=1,
            split="development",
            questions=development,
        ),
        "validation": CiderQASplitDataset(
            schema_version=1,
            split="validation",
            questions=validation,
        ),
        "final_test": CiderQASplitDataset(
            schema_version=1,
            split="final_test",
            questions=final_test,
        ),
    }

    report = assess_ciderqa_readiness(datasets)

    assert report.structurally_ready is True
    assert report.question_count == 100
    assert report.full_text_question_count == 25
    assert report.unanswerable_question_count == 15
    assert report.multi_source_question_count == 20
    assert report.language_counts == {"fr": 50, "en": 50}
    assert report.expert_validation_required is True


def test_ciderqa_readiness_reports_every_missing_quota() -> None:
    dataset = CiderQASplitDataset(
        schema_version=1,
        split="development",
        questions=[_question(99, "development")],
    )

    report = assess_ciderqa_readiness({"development": dataset})

    assert report.structurally_ready is False
    assert set(report.failures) == {
        "question_count_below_100",
        "required_splits_missing",
        "full_text_question_count_below_25",
        "unanswerable_question_count_below_15",
        "multi_source_question_count_below_20",
        "language_balance_outside_45_55_percent",
    }
