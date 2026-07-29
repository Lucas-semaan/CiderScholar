"""Multilingual Cross-Encoder Reranker for hybrid retrieval and Deep Research."""

from __future__ import annotations

import gc
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.desktop.model_integrity import verify_model_manifest
from app.ingestion.embeddings import model_storage_name

logger = logging.getLogger(__name__)


class LocalRerankerModelNotFoundError(FileNotFoundError):
    """Raised instead of silently contacting a model registry."""


def local_reranker_model_path(settings: Settings) -> Path:
    return settings.paths.models_dir / model_storage_name(settings.reranker.model_name)


class RerankerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    original_score: float = Field(default=0.0)


class RerankedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    text: str
    original_score: float
    rerank_score: float
    combined_score: float


class MultilingualReranker:
    def __init__(
        self,
        *,
        enabled: bool = False,
        model_name: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        model_path: str | Path | None = None,
        device: str = "cpu",
        batch_size: int = 4,
        reranker_weight: float = 0.20,
        local_files_only: bool = True,
        trust_remote_code: bool = False,
    ) -> None:
        if not local_files_only or trust_remote_code:
            raise ValueError("the reranker must use local files without remote code")
        self.enabled = enabled
        self.model_name = model_name
        self.model_path = Path(model_path).resolve() if model_path is not None else None
        self.device = device
        self.batch_size = batch_size
        self.reranker_weight = reranker_weight
        self.local_files_only = local_files_only
        self.trust_remote_code = trust_remote_code
        self._model: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> MultilingualReranker:
        return cls(
            enabled=settings.reranker.enabled,
            model_name=settings.reranker.model_name,
            model_path=local_reranker_model_path(settings),
            device=settings.reranker.device,
            batch_size=settings.reranker.batch_size,
            reranker_weight=settings.retrieval.reranker_weight,
            local_files_only=settings.reranker.local_files_only,
            trust_remote_code=settings.reranker.trust_remote_code,
        )

    def _load_model(self) -> Any:
        if self._model is None:
            if self.model_path is None or not self.model_path.is_dir():
                raise LocalRerankerModelNotFoundError(
                    "local reranker model not found; run "
                    "python -m scripts.prepare_reranker_model --allow-network"
                )
            verify_model_manifest(self.model_path, self.model_name)
            from sentence_transformers import CrossEncoder

            logger.info("Loading local CrossEncoder model path=%s", self.model_path)
            self._model = CrossEncoder(
                str(self.model_path),
                device=self.device,
                local_files_only=True,
                trust_remote_code=False,
            )
        return self._model

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankerCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankedResult]:
        if not candidates:
            return []

        if not self.enabled:
            results = [
                RerankedResult(
                    candidate_id=cand.candidate_id,
                    text=cand.text,
                    original_score=cand.original_score,
                    rerank_score=cand.original_score,
                    combined_score=cand.original_score,
                )
                for cand in candidates
            ]
            results.sort(key=lambda x: x.combined_score, reverse=True)
            return results[:top_k] if top_k is not None else results

        model = self._load_model()
        pairs = [[query, cand.text] for cand in candidates]
        scores = model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )

        results = []
        for cand, raw_score in zip(candidates, scores, strict=True):
            r_score = float(raw_score)
            c_score = (
                1.0 - self.reranker_weight
            ) * cand.original_score + self.reranker_weight * r_score
            results.append(
                RerankedResult(
                    candidate_id=cand.candidate_id,
                    text=cand.text,
                    original_score=cand.original_score,
                    rerank_score=r_score,
                    combined_score=c_score,
                )
            )

        results.sort(key=lambda x: x.combined_score, reverse=True)
        return results[:top_k] if top_k is not None else results

    def close(self) -> None:
        model = self._model
        self._model = None
        if model is not None:
            logger.info("Unloading CrossEncoder model: %s", self.model_name)
            del model
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
