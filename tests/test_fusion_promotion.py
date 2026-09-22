from __future__ import annotations

from app.evaluation.ciderqa_promotion import PromotionDecision
from app.evaluation.fusion_promotion import decide_fusion_promotion


def test_fusion_promotion_requires_a_passing_gate_and_explicit_user_approval() -> None:
    rejected = decide_fusion_promotion(
        PromotionDecision(promoted=False, failures=["citation precision"]),
        ablation_report_sha256="a" * 64,
        user_approved=True,
    )
    experimental = decide_fusion_promotion(
        PromotionDecision(promoted=True, failures=[]), ablation_report_sha256="a" * 64
    )
    proposed = decide_fusion_promotion(
        PromotionDecision(promoted=True, failures=[]),
        ablation_report_sha256="a" * 64,
        user_approved=True,
    )

    assert rejected.state == "rejected"
    assert experimental.state == "experimental"
    assert proposed.state == "promotion_proposed"
    assert proposed.release_flag_enabled is False
    assert proposed.rollback_to_single_wave is True
