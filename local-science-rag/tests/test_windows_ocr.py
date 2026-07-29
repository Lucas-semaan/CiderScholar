from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ingestion.pdf_extractor import OcrPageTrace
from app.ingestion.windows_ocr import ocr_text_confidence


def test_ocr_text_quality_confidence_is_deterministic_and_bounded() -> None:
    scientific = (
        "La concentration en polyphénols augmente après dix jours de fermentation contrôlée."
    )
    noisy = "@@@ ### ???"

    assert ocr_text_confidence(scientific) >= 0.75
    assert ocr_text_confidence(noisy) < 0.75
    assert ocr_text_confidence("") == 0.0
    assert ocr_text_confidence(scientific) == ocr_text_confidence(scientific)


def test_ocr_trace_cannot_mark_low_confidence_reason_as_admitted() -> None:
    with pytest.raises(ValidationError, match="admission"):
        OcrPageTrace(
            page_number=2,
            language="fr-FR",
            confidence=0.4,
            embedded_text_original="x",
            ocr_text="texte incertain",
            admitted=True,
            decision_reason="ocr_low_confidence",
        )
