from __future__ import annotations

import pytest

from app.config import load_settings
from app.desktop.model_integrity import verify_model_manifest
from app.retrieval.reranker import (
    MultilingualReranker,
    RerankerCandidate,
    local_reranker_model_path,
)


def test_packaged_reranker_load_predict_close_cycle_when_model_is_present() -> None:
    settings = load_settings()
    model_path = local_reranker_model_path(settings)
    if not model_path.is_dir():
        pytest.skip("the optional packaged reranker is absent from this checkout")
    verify_model_manifest(model_path, settings.reranker.model_name)
    reranker = MultilingualReranker(
        enabled=True,
        model_name=settings.reranker.model_name,
        model_path=model_path,
        device="cpu",
        batch_size=2,
    )
    candidates = [
        RerankerCandidate(
            candidate_id="relevant",
            text="La température influence la fermentation du cidre.",
        ),
        RerankerCandidate(
            candidate_id="irrelevant",
            text="La géologie des fonds océaniques.",
        ),
    ]

    results = reranker.rerank("température de fermentation du cidre", candidates)

    assert {result.candidate_id for result in results} == {"relevant", "irrelevant"}
    assert all(isinstance(result.rerank_score, float) for result in results)
    assert reranker._model is not None
    reranker.close()
    assert reranker._model is None
