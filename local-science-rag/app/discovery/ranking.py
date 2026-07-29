"""Observable rubric, deterministic BTL ranking, and blind-judge calibration."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CriterionName = Literal[
    "plausibility",
    "novelty",
    "testability",
    "evidence_quality",
    "cost",
    "risk",
    "limitations",
]


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: CriterionName
    low_anchor: str = Field(min_length=10, max_length=500)
    high_anchor: str = Field(min_length=10, max_length=500)


class PairwiseRubric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    criteria: list[RubricCriterion] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def fixed_criteria(self) -> PairwiseRubric:
        expected = {
            "plausibility",
            "novelty",
            "testability",
            "evidence_quality",
            "cost",
            "risk",
            "limitations",
        }
        if {criterion.name for criterion in self.criteria} != expected:
            raise ValueError("pairwise rubric requires all seven observable criteria")
        return self


DEFAULT_RUBRIC = PairwiseRubric(
    version="1.0.0",
    criteria=[
        RubricCriterion(
            name="plausibility",
            low_anchor="Mécanisme contredit ou sans prémisses étayées.",
            high_anchor="Mécanisme cohérent avec toutes les prémisses étayées.",
        ),
        RubricCriterion(
            name="novelty",
            low_anchor="Reformule directement un résultat déjà établi.",
            high_anchor="Produit une relation testable absente des preuves consultées.",
        ),
        RubricCriterion(
            name="testability",
            low_anchor="Aucune observation ne peut départager l’hypothèse.",
            high_anchor="Une issue mesurable distingue clairement les alternatives.",
        ),
        RubricCriterion(
            name="evidence_quality",
            low_anchor="Dépend de preuves indirectes, faibles ou contradictoires.",
            high_anchor="Repose sur plusieurs observations directes et convergentes.",
        ),
        RubricCriterion(
            name="cost",
            low_anchor="Mobilise des ressources non chiffrées ou indisponibles.",
            high_anchor="Les ressources nécessaires sont bornées et accessibles.",
        ),
        RubricCriterion(
            name="risk",
            low_anchor="Les risques sont inconnus ou non maîtrisables.",
            high_anchor="Les risques identifiés sont faibles et soumis à revue.",
        ),
        RubricCriterion(
            name="limitations",
            low_anchor="Ignore les conditions invalidantes et les données manquantes.",
            high_anchor="Expose explicitement limites, contradictions et lacunes.",
        ),
    ],
)


class PairwiseComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left_id: str
    right_id: str
    winner_id: str
    judge_reference: str = Field(min_length=3, max_length=200)
    left_presented_first: bool

    @model_validator(mode="after")
    def valid_pair(self) -> PairwiseComparison:
        if self.left_id == self.right_id:
            raise ValueError("pairwise candidates must differ")
        if self.winner_id not in {self.left_id, self.right_id}:
            raise ValueError("winner must be one of the compared candidates")
        return self


class BTLRank(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis_id: str
    rank: int = Field(ge=1)
    ability: float = Field(gt=0)
    log_ability: float
    standard_error: float = Field(ge=0)
    comparison_count: int = Field(ge=1)


def fit_bradley_terry_luce(
    comparisons: list[PairwiseComparison],
    *,
    iterations: int = 200,
    tolerance: float = 1e-10,
) -> list[BTLRank]:
    candidates = sorted(
        {
            candidate
            for comparison in comparisons
            for candidate in (comparison.left_id, comparison.right_id)
        }
    )
    if len(candidates) < 2:
        raise ValueError("BTL ranking requires at least two candidates")
    wins = defaultdict(lambda: 0.5)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    total_counts: dict[str, int] = defaultdict(int)
    for comparison in comparisons:
        wins[comparison.winner_id] += 1
        pair = tuple(sorted((comparison.left_id, comparison.right_id)))
        counts[pair] += 1
        total_counts[comparison.left_id] += 1
        total_counts[comparison.right_id] += 1
    abilities = {candidate: 1.0 for candidate in candidates}
    for _ in range(iterations):
        updated = {}
        for candidate in candidates:
            denominator = 0.0
            for other in candidates:
                if candidate == other:
                    continue
                observations = counts.get(tuple(sorted((candidate, other))), 0)
                if observations:
                    denominator += observations / (abilities[candidate] + abilities[other])
            updated[candidate] = (
                wins[candidate] / denominator if denominator else abilities[candidate]
            )
        geometric_mean = math.exp(
            sum(math.log(max(value, 1e-12)) for value in updated.values()) / len(updated)
        )
        updated = {key: value / geometric_mean for key, value in updated.items()}
        difference = max(abs(updated[key] - abilities[key]) for key in candidates)
        abilities = updated
        if difference < tolerance:
            break
    ordered = sorted(candidates, key=lambda item: (-abilities[item], item))
    return [
        BTLRank(
            hypothesis_id=candidate,
            rank=index,
            ability=abilities[candidate],
            log_ability=math.log(abilities[candidate]),
            standard_error=1 / math.sqrt(total_counts[candidate]),
            comparison_count=total_counts[candidate],
        )
        for index, candidate in enumerate(ordered, start=1)
    ]


class BlindCalibrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concordance: float = Field(ge=0, le=1)
    intra_judge_stability: float = Field(ge=0, le=1)
    first_position_win_rate: float = Field(ge=0, le=1)
    comparison_count: int = Field(ge=1)
    repeated_pair_count: int = Field(ge=1)


def calibrate_blind_judge(
    expert: list[PairwiseComparison],
    judge: list[PairwiseComparison],
) -> BlindCalibrationResult:
    def key(item: PairwiseComparison) -> tuple[str, str]:
        return tuple(sorted((item.left_id, item.right_id)))

    expert_by_pair = {key(item): item.winner_id for item in expert}
    matched = [item for item in judge if key(item) in expert_by_pair]
    if not matched:
        raise ValueError("blind calibration has no shared expert pairs")
    histories: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in judge:
        histories[key(item)].append(item.winner_id)
    repeated = [values for values in histories.values() if len(values) > 1]
    if not repeated:
        raise ValueError("blind calibration requires repeated pairs")
    stability = sum(len(set(values)) == 1 for values in repeated) / len(repeated)
    first_wins = sum(
        item.winner_id == (item.left_id if item.left_presented_first else item.right_id)
        for item in judge
    )
    return BlindCalibrationResult(
        concordance=sum(item.winner_id == expert_by_pair[key(item)] for item in matched)
        / len(matched),
        intra_judge_stability=stability,
        first_position_win_rate=first_wins / len(judge),
        comparison_count=len(matched),
        repeated_pair_count=len(repeated),
    )
