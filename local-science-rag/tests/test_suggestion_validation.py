from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.suggestions.models import SuggestionDraft
from app.suggestions.validation import (
    SuggestionValidationError,
    normalize_suggestion_doi,
    validate_pdf_payload,
    validate_reference_url,
)


def test_suggestion_schema_rejects_unknown_fields_and_normalizes_doi() -> None:
    payload = {
        "schema_version": 1,
        "created_at": datetime(2026, 7, 22, tzinfo=UTC),
        "source": {"kind": "doi", "doi": "https://doi.org/10.1000/ABC.1"},
    }

    draft = SuggestionDraft.model_validate(payload)

    assert draft.source.doi == "10.1000/abc.1"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SuggestionDraft.model_validate({**payload, "remote_status": "accepted"})


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "doi"},
        {"kind": "url"},
        {"kind": "pdf"},
        {"kind": "manual", "title": "Titre seulement"},
    ],
)
def test_each_suggestion_variant_requires_its_own_fields(source) -> None:
    with pytest.raises(ValidationError):
        SuggestionDraft(created_at=datetime.now(UTC), source=source)


def test_comment_is_bounded_and_cannot_add_schema_fields() -> None:
    with pytest.raises(ValidationError):
        SuggestionDraft(
            created_at=datetime.now(UTC),
            source={"kind": "doi", "doi": "10.1000/test"},
            scientific_comment="x" * 1501,
        )


@pytest.mark.parametrize("value", ["invalid", "doi: 123", "10.1/short"])
def test_invalid_doi_is_rejected_locally(value: str) -> None:
    with pytest.raises(SuggestionValidationError, match="DOI invalide"):
        normalize_suggestion_doi(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://example.org/article",
        "https://user:secret@example.org/article",
        "https://localhost/article",
        "https://127.0.0.1/article",
        "https://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ],
)
def test_dangerous_url_is_rejected_without_network(value: str) -> None:
    with pytest.raises(SuggestionValidationError):
        validate_reference_url(value)


def test_url_remains_a_reference_and_valid_pdf_gets_only_an_internal_name() -> None:
    assert validate_reference_url("https://example.org/article") == ("https://example.org/article")
    internal, digest = validate_pdf_payload(
        "../../secret name.pdf",
        b"%PDF-1.7\nbody",
        maximum_bytes=100,
    )

    assert internal.startswith("suggestion-")
    assert "secret" not in internal
    assert len(digest) == 64


def test_fake_pdf_is_rejected_before_any_other_processing() -> None:
    with pytest.raises(SuggestionValidationError, match="signature PDF"):
        validate_pdf_payload("paper.pdf", b"not a pdf", maximum_bytes=100)
