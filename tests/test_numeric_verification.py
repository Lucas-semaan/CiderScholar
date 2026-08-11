from __future__ import annotations

from app.numeric_verification import (
    NumericIssueCode,
    NumericVerdict,
    verify_numeric_claim,
)


def test_supports_exact_value_unit_comparator_and_condition() -> None:
    report = verify_numeric_claim(
        "Methanol concentration was below 200 mg/L after 10 days.",
        {"evidence-methanol": ("Methanol concentration was below 200 mg/L after 10 days.")},
    )

    assert report.verdict is NumericVerdict.SUPPORTED
    assert [assessment.source_id for assessment in report.assessments] == [
        "evidence-methanol",
        "evidence-methanol",
    ]


def test_supports_decimal_separator_alias_and_ph() -> None:
    report = verify_numeric_claim(
        "The pH was 3,5.",
        {"evidence-ph": "The pH was 3.5."},
    )

    assert report.verdict is NumericVerdict.SUPPORTED
    assert report.assessments[0].quantity.unit == "ph"


def test_rejects_unit_scale_without_implicit_conversion() -> None:
    report = verify_numeric_claim(
        "Methanol concentration was 200 g/L.",
        {"evidence-methanol": "Methanol concentration was 200 mg/L."},
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.UNSUPPORTED_CONVERSION in report.issues


def test_rejects_unannounced_unit_conversion_when_values_differ() -> None:
    report = verify_numeric_claim(
        "Methanol concentration was 1 mg/L.",
        {"evidence-methanol": "Methanol concentration was 1000 µg/L."},
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.UNSUPPORTED_CONVERSION in report.issues


def test_rejects_reversed_comparator() -> None:
    report = verify_numeric_claim(
        "Methanol concentration was above 200 mg/L.",
        {"evidence-methanol": "Methanol concentration was below 200 mg/L."},
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.OPERATOR_MISMATCH in report.issues


def test_rejects_reversed_direction_with_same_percentage() -> None:
    report = verify_numeric_claim(
        "Ester concentration decreased by 3 %.",
        {"evidence-esters": "Ester concentration increased by 3 %."},
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.DIRECTION_MISMATCH in report.issues


def test_rejects_changed_signed_value() -> None:
    report = verify_numeric_claim(
        "The acidity delta was +3 mg/L.",
        {"evidence-acidity": "The acidity delta was -3 mg/L."},
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.SIGN_MISMATCH in report.issues


def test_rejects_changed_range_and_uncertainty() -> None:
    range_report = verify_numeric_claim(
        "Temperature remained between 10 and 25 °C.",
        {"evidence-temperature": "Temperature remained between 10 and 20 °C."},
    )
    uncertainty_report = verify_numeric_claim(
        "Acetaldehyde was 3.0 ± 0.3 mg/L.",
        {"evidence-acetaldehyde": "Acetaldehyde was 3.0 ± 0.2 mg/L."},
    )

    assert range_report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.RANGE_MISMATCH in range_report.issues
    assert uncertainty_report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.UNCERTAINTY_MISMATCH in uncertainty_report.issues


def test_rejects_context_collision_when_values_are_reused() -> None:
    report = verify_numeric_claim(
        "Methanol was 10 mg/L after 7 days.",
        {
            "evidence-mixture": (
                "Ethanol was 10 mg/L after 7 days. Methanol was 10 mg/L after 14 days."
            )
        },
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.CONTEXT_MISMATCH in report.issues


def test_does_not_join_value_and_context_across_evidence_items() -> None:
    report = verify_numeric_claim(
        "Acetaldehyde concentration was 20 mg/L.",
        {
            "evidence-methanol": "Methanol concentration was 20 mg/L.",
            "evidence-ethanol": "Ethanol concentration was 20 mg/L.",
        },
    )

    assert report.verdict is NumericVerdict.UNSUPPORTED
    assert NumericIssueCode.CONTEXT_MISMATCH in report.issues


def test_marks_cross_language_context_without_shared_anchor_as_ambiguous() -> None:
    report = verify_numeric_claim(
        "Le methanol était de 4 mg/L.",
        {"evidence-ethanol": "Ethanol was 4 mg/L."},
    )

    assert report.verdict is NumericVerdict.AMBIGUOUS
    assert NumericIssueCode.CONTEXT_AMBIGUOUS in report.issues


def test_locators_are_not_treated_as_scientific_quantities() -> None:
    report = verify_numeric_claim(
        "Figure 2 appears on page 4.",
        {"evidence-figure": "Figure 2 appears on page 4."},
    )

    assert report.verdict is NumericVerdict.NOT_APPLICABLE
    assert not report.assessments


def test_unparsed_scientific_notation_is_ambiguous_not_silently_supported() -> None:
    report = verify_numeric_claim(
        "The count was 1.0 × 10^3 CFU/mL.",
        {"evidence-count": "The count was 1.0 × 10^3 CFU/mL."},
    )

    assert report.verdict is NumericVerdict.AMBIGUOUS
    assert NumericIssueCode.UNPARSED_NUMERIC in report.issues


def test_report_does_not_expose_evidence_text() -> None:
    secret = "sensitive-evidence-text-keep-private"
    report = verify_numeric_claim(
        "Methanol was 4 mg/L.",
        {"evidence-private": f"{secret}: ethanol was 4 mg/L."},
    )

    assert secret not in repr(report)
    assert secret not in str(report)
