"""Human-gated promotion decisions for the evaluation-only Fusion path."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.ciderqa_promotion import PromotionDecision

FusionPromotionState = Literal["rejected", "experimental", "promotion_proposed"]


class FusionPromotionDecision(BaseModel):
    """A promotion recommendation; it never changes runtime retrieval configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: FusionPromotionState
    ablation_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    user_approved: bool = False
    release_flag_enabled: Literal[False] = False
    rollback_to_single_wave: Literal[True] = True
    reasons: tuple[str, ...] = Field(default=(), max_length=30)


def decide_fusion_promotion(
    gate: PromotionDecision,
    *,
    ablation_report_sha256: str,
    user_approved: bool = False,
) -> FusionPromotionDecision:
    if not gate.promoted:
        return FusionPromotionDecision(
            state="rejected",
            ablation_report_sha256=ablation_report_sha256,
            user_approved=user_approved,
            reasons=tuple(gate.failures),
        )
    if not user_approved:
        return FusionPromotionDecision(
            state="experimental",
            ablation_report_sha256=ablation_report_sha256,
            reasons=("explicit user approval is required before proposing promotion",),
        )
    return FusionPromotionDecision(
        state="promotion_proposed",
        ablation_report_sha256=ablation_report_sha256,
        user_approved=True,
        reasons=("scientific gate passed; release flag remains disabled pending implementation",),
    )
