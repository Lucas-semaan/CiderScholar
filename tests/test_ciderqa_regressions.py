from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.evaluation.ciderqa_regressions import (
    REGRESSION_CATEGORIES,
    CiderQARegressionCase,
    SignedCiderQARegressionPackage,
)


def _case(category: str, index: int) -> CiderQARegressionCase:
    return CiderQARegressionCase(
        category=category,
        question_id=f"ciderqa-regression-{index}",
        rationale="Erreur représentative observée et classée par un expert.",
        baseline_value=0.0,
        required_value=1.0,
    )


def test_regression_package_requires_six_distinct_real_error_categories() -> None:
    package = SignedCiderQARegressionPackage(
        created_at="2026-07-27T10:00:00Z",
        dataset_sha256="a" * 64,
        split="validation",
        source_report_sha256="b" * 64,
        cases=[_case(category, index) for index, category in enumerate(REGRESSION_CATEGORIES)],
        package_sha256="c" * 64,
    )

    assert {case.category for case in package.cases} == set(REGRESSION_CATEGORIES)

    duplicated = [_case(category, 0) for category in REGRESSION_CATEGORIES]
    with pytest.raises(ValidationError, match="distinct questions"):
        SignedCiderQARegressionPackage(
            created_at="2026-07-27T10:00:00Z",
            dataset_sha256="a" * 64,
            split="validation",
            source_report_sha256="b" * 64,
            cases=duplicated,
            package_sha256="c" * 64,
        )
