from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.corpus_packages.distribution import create_distribution_layout
from app.suggestions.models import (
    DoiSuggestionSource,
    SuggestionArgoDecision,
    SuggestionCandidateContext,
)
from app.suggestions.packaging import (
    retry_pending_packages,
    suggestion_outbox,
    suggestion_receipts,
)
from app.suggestions.service import submit_pdf_suggestion, submit_reference_suggestion


def _decision(*, accepted: bool = True) -> SuggestionArgoDecision:
    return SuggestionArgoDecision(
        relevant=accepted,
        reason="Le document traite directement de la fermentation cidricole.",
        theme="fermentation",
        uncertainty="low",
        confidence=0.95 if accepted else 0.2,
    )


def _distribution(settings, root: Path):
    configured = settings.model_copy(deep=True)
    configured.distribution.enabled = True
    configured.distribution.synchronized_root = root
    create_distribution_layout(root)
    return configured


def test_accepted_doi_is_packaged_atomically_with_minimal_receipt(settings, tmp_path) -> None:
    configured = _distribution(settings, tmp_path / "CiderScholar")

    result = submit_reference_suggestion(
        configured,
        DoiSuggestionSource(doi="10.1000/cider"),
        scientific_comment="Résultats utiles pour la fermentation.",
        evaluator=lambda *_: _decision(),
    )

    assert result.state == "accepted"
    assert result.message == "Suggestion acceptée et transmise."
    destination = (
        configured.distribution.synchronized_root
        / "suggestions"
        / "inbox"
        / str(result.suggestion_id)
    )
    assert sorted(path.name for path in destination.iterdir()) == ["suggestion.json"]
    manifest = json.loads((destination / "suggestion.json").read_text(encoding="utf-8"))
    assert manifest["source"]["doi"] == "10.1000/cider"
    assert "argo" not in json.dumps(manifest).casefold()
    assert not list(suggestion_outbox(configured).glob("*"))
    receipts = list(suggestion_receipts(configured).glob("*.json"))
    assert len(receipts) == 1
    assert set(json.loads(receipts[0].read_text(encoding="utf-8"))) == {
        "suggestion_id",
        "submitted_at",
        "package_sha256",
    }


def test_conservative_rejection_creates_no_package(settings) -> None:
    result = submit_reference_suggestion(
        settings,
        DoiSuggestionSource(doi="10.1000/off-topic"),
        evaluator=lambda *_: _decision(accepted=False),
    )

    assert result.state == "not_retained"
    assert not suggestion_outbox(settings).exists()


def test_unavailable_sharepoint_keeps_one_retryable_package(settings, tmp_path) -> None:
    configured = settings.model_copy(deep=True)
    root = tmp_path / "CiderScholar"
    configured.distribution.enabled = True
    configured.distribution.synchronized_root = root

    result = submit_reference_suggestion(
        configured,
        DoiSuggestionSource(doi="10.1000/retry"),
        evaluator=lambda *_: _decision(),
    )

    assert result.state == "retry"
    assert len(list(suggestion_outbox(configured).glob("*/suggestion.json"))) == 1
    create_distribution_layout(root)

    assert retry_pending_packages(configured) == 1
    assert retry_pending_packages(configured) == 0
    assert len(list((root / "suggestions" / "inbox").glob("*/suggestion.json"))) == 1


def test_duplicate_doi_is_rejected_before_second_argo_call(settings, tmp_path) -> None:
    configured = _distribution(settings, tmp_path / "CiderScholar")
    calls = 0

    def evaluator(*_args):
        nonlocal calls
        calls += 1
        return _decision()

    first = submit_reference_suggestion(
        configured,
        DoiSuggestionSource(doi="10.1000/duplicate"),
        evaluator=evaluator,
    )
    duplicate = submit_reference_suggestion(
        configured,
        DoiSuggestionSource(doi="10.1000/duplicate"),
        evaluator=evaluator,
    )

    assert first.state == "accepted"
    assert duplicate.state == "not_retained"
    assert calls == 1


def test_pdf_requires_confirmation_before_validation_or_argo(settings) -> None:
    with pytest.raises(ValueError, match="confirmée explicitement"):
        submit_pdf_suggestion(
            settings,
            filename="paper.pdf",
            payload=b"not a pdf",
            scientific_comment=None,
            transmit_pdf_confirmed=False,
            evaluator=lambda *_: _decision(),
        )


def test_pdf_package_uses_validated_hash_and_safe_internal_name(
    settings,
    tmp_path,
    monkeypatch,
) -> None:
    configured = _distribution(settings, tmp_path / "CiderScholar")
    monkeypatch.setattr(
        "app.suggestions.service.extract_pdf_candidate",
        lambda *_args, **_kwargs: SuggestionCandidateContext(
            title="Étude cidricole",
            doi="10.1000/pdf",
            abstract="Fermentation de cidre.",
        ),
    )

    result = submit_pdf_suggestion(
        configured,
        filename="../../nom privé.pdf",
        payload=b"%PDF-1.7\nvalidated content",
        scientific_comment=None,
        transmit_pdf_confirmed=True,
        evaluator=lambda *_: _decision(),
    )

    destination = (
        configured.distribution.synchronized_root
        / "suggestions"
        / "inbox"
        / str(result.suggestion_id)
    )
    pdfs = list(destination.glob("*.pdf"))
    assert result.state == "accepted"
    assert len(pdfs) == 1
    assert pdfs[0].name.startswith("suggestion-")
    assert "privé" not in pdfs[0].name
