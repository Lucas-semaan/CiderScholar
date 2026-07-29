from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.desktop.model_integrity import ModelIntegrityError, write_model_manifest
from app.retrieval.reranker import (
    LocalRerankerModelNotFoundError,
    MultilingualReranker,
    RerankerCandidate,
)


def test_reranker_disabled_by_default_returns_original_scores() -> None:
    reranker = MultilingualReranker(enabled=False)
    candidates = [
        RerankerCandidate(candidate_id="c1", text="Fermentation du cidre", original_score=0.8),
        RerankerCandidate(candidate_id="c2", text="Pommes de Normandie", original_score=0.9),
    ]

    results = reranker.rerank("cidre", candidates)

    assert len(results) == 2
    assert results[0].candidate_id == "c2"
    assert results[0].combined_score == 0.9
    assert results[1].candidate_id == "c1"
    assert results[1].combined_score == 0.8
    assert reranker._model is None


def test_reranker_empty_candidates_returns_empty_list() -> None:
    reranker = MultilingualReranker(enabled=True)
    results = reranker.rerank("cidre", [])
    assert results == []


def test_reranker_enabled_predicts_and_combines_scores() -> None:
    reranker = MultilingualReranker(enabled=True, reranker_weight=0.2)
    mock_model = MagicMock()
    mock_model.predict.return_value = [0.95, 0.50]

    candidates = [
        RerankerCandidate(candidate_id="c1", text="Fermentation levures", original_score=0.7),
        RerankerCandidate(candidate_id="c2", text="Autre sujet", original_score=0.6),
    ]

    with patch.object(reranker, "_load_model", return_value=mock_model):
        results = reranker.rerank("fermentation", candidates)

    assert len(results) == 2
    assert results[0].candidate_id == "c1"
    # c1 score: (1 - 0.2) * 0.7 + 0.2 * 0.95 = 0.56 + 0.19 = 0.75
    assert pytest.approx(results[0].combined_score, 0.001) == 0.75
    assert results[0].rerank_score == 0.95
    assert mock_model.predict.called


def test_reranker_close_unloads_model() -> None:
    reranker = MultilingualReranker(enabled=True)
    reranker._model = MagicMock()

    reranker.close()

    assert reranker._model is None


def test_enabled_reranker_refuses_registry_name_without_local_directory() -> None:
    reranker = MultilingualReranker(enabled=True)

    with pytest.raises(LocalRerankerModelNotFoundError, match="local reranker model"):
        reranker.rerank(
            "fermentation",
            [RerankerCandidate(candidate_id="c1", text="Levures")],
        )


def test_reranker_verifies_manifest_and_forces_local_cross_encoder(tmp_path) -> None:
    model_path = tmp_path / "reranker"
    model_path.mkdir()
    weights = model_path / "model.safetensors"
    weights.write_bytes(b"local weights")
    write_model_manifest(model_path, "org/reranker")
    model = MagicMock()
    cross_encoder = MagicMock(return_value=model)
    reranker = MultilingualReranker(
        enabled=True,
        model_name="org/reranker",
        model_path=model_path,
        device="cpu",
    )

    with patch.dict(
        sys.modules,
        {"sentence_transformers": SimpleNamespace(CrossEncoder=cross_encoder)},
    ):
        assert reranker._load_model() is model

    cross_encoder.assert_called_once_with(
        str(model_path.resolve()),
        device="cpu",
        local_files_only=True,
        trust_remote_code=False,
    )

    reranker.close()
    weights.write_bytes(b"tampered")
    with pytest.raises(ModelIntegrityError, match="hash mismatch"):
        reranker._load_model()
