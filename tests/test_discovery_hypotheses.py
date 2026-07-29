from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.discovery.contracts import (
    DiscriminatingExperiment,
    HumanHypothesisReview,
    HypothesisDraft,
    HypothesisPremise,
    build_hypothesis_card,
)
from app.discovery.ranking import (
    DEFAULT_RUBRIC,
    PairwiseComparison,
    calibrate_blind_judge,
    fit_bradley_terry_luce,
)
from app.discovery.repository import DiscoveryRepository


def _card(*, hypothesis_id=None, prediction: str = "La variable mesurée diminuera nettement."):
    return build_hypothesis_card(
        question="Pourquoi la variable diminue-t-elle pendant la fermentation ?",
        draft=HypothesisDraft(
            premises=[
                HypothesisPremise(
                    statement="Une diminution reproductible est observée dans les données.",
                    evidence_ids=["claim-1"],
                )
            ],
            contradictions=["Une étude rapporte une stabilité dans une autre population."],
            uncertainties=["La dépendance au cultivar reste inconnue."],
            explicit_gaps=["Aucune mesure temporelle indépendante n’est disponible."],
            testable_prediction=prediction,
            discriminating_experiment=DiscriminatingExperiment(
                principle="Comparer deux conditions contrôlées définies par un expert.",
                discriminating_outcome=(
                    "Une différence directionnelle départage les mécanismes proposés."
                ),
                safety_review_required=True,
                executable_protocol=False,
            ),
        ),
        validated_evidence_ids={"claim-1"},
        corpus_sha256="a" * 64,
        model_sha256="b" * 64,
        prompt_sha256="c" * 64,
        hypothesis_id=hypothesis_id,
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
    )


def test_hypothesis_uses_only_validated_evidence_and_forbids_protocols() -> None:
    card = _card()
    assert card.premises[0].evidence_ids == ["claim-1"]
    with pytest.raises(ValueError, match="unvalidated evidence"):
        build_hypothesis_card(
            question=card.question,
            draft=HypothesisDraft(
                premises=[
                    HypothesisPremise(
                        statement="Cette prémisse ne possède pas de preuve validée.",
                        evidence_ids=["unknown"],
                    )
                ],
                contradictions=["Une contradiction documentée reste ouverte."],
                uncertainties=["Une incertitude documentée reste ouverte."],
                explicit_gaps=["Une donnée expérimentale reste manquante."],
                testable_prediction="Une variation observable distinguera les mécanismes.",
                discriminating_experiment=card.discriminating_experiment,
            ),
            validated_evidence_ids={"claim-1"},
            corpus_sha256="a" * 64,
            model_sha256="b" * 64,
            prompt_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="laboratory instructions"):
        DiscriminatingExperiment(
            principle="Ajoutez 10 mL puis mesurez le résultat.",
            discriminating_outcome="Une différence mesurée permettrait de conclure.",
            safety_review_required=True,
            executable_protocol=False,
        )


def test_versions_are_append_only_and_retention_requires_explicit_review(settings) -> None:
    repository = DiscoveryRepository(settings.paths.database_path)
    repository.initialize()
    hypothesis_id = uuid4()
    first_version, first_hash = repository.append_hypothesis_version(
        _card(hypothesis_id=hypothesis_id)
    )
    second_version, second_hash = repository.append_hypothesis_version(
        _card(
            hypothesis_id=hypothesis_id,
            prediction="La variable mesurée augmentera dans la condition discriminante.",
        )
    )

    assert (first_version, second_version) == (1, 2)
    assert first_hash != second_hash
    assert repository.status(hypothesis_id) == "draft"
    repository.review(
        hypothesis_id,
        second_version,
        HumanHypothesisReview(
            decision="retain",
            expert_reference="expert-local-1",
            comment="Version retenue après revue.",
            created_at=datetime(2026, 7, 27, 12, tzinfo=UTC),
        ),
    )
    assert repository.status(hypothesis_id) == "retained"
    with (
        repository.database.connect() as connection,
        pytest.raises(
            sqlite3.IntegrityError,
            match="immutable",
        ),
    ):
        connection.execute(
            """
            UPDATE discovery_hypothesis_versions SET content_json = '{}'
            WHERE hypothesis_id = ? AND version = 1
            """,
            (str(hypothesis_id),),
        )


def test_btl_ranking_and_blind_calibration_are_reproducible() -> None:
    comparisons = [
        PairwiseComparison(
            left_id="A",
            right_id="B",
            winner_id="A",
            judge_reference="judge-1",
            left_presented_first=True,
        ),
        PairwiseComparison(
            left_id="A",
            right_id="C",
            winner_id="A",
            judge_reference="judge-1",
            left_presented_first=False,
        ),
        PairwiseComparison(
            left_id="B",
            right_id="C",
            winner_id="B",
            judge_reference="judge-1",
            left_presented_first=True,
        ),
    ]
    ranking = fit_bradley_terry_luce(comparisons)
    assert [item.hypothesis_id for item in ranking] == ["A", "B", "C"]
    assert all(item.standard_error > 0 for item in ranking)
    assert len(DEFAULT_RUBRIC.criteria) == 7

    expert = [comparisons[0]]
    judge = [comparisons[0], comparisons[0].model_copy(update={"left_presented_first": False})]
    calibration = calibrate_blind_judge(expert, judge)
    assert calibration.concordance == 1
    assert calibration.intra_judge_stability == 1
    assert calibration.first_position_win_rate == 0.5
