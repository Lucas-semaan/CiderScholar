"""Append-only SQLite persistence for hypotheses and explicit expert reviews."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.database.sqlite import Database
from app.discovery.analysis import AnalysisRecord, DiscoveryCycleApproval
from app.discovery.contracts import HumanHypothesisReview, HypothesisCard, content_hash
from app.discovery.ranking import BTLRank, PairwiseComparison


class DiscoveryRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database = Database(database_path)

    def initialize(self) -> None:
        self.database.initialize()

    def append_hypothesis_version(self, card: HypothesisCard) -> tuple[int, str]:
        timestamp = card.created_at.astimezone(UTC).isoformat()
        content = card.model_dump(mode="json")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT current_version FROM discovery_hypotheses WHERE id = ?",
                (str(card.id),),
            ).fetchone()
            current_version = int(row["current_version"]) if row else 0
            parent = None
            if current_version:
                parent = connection.execute(
                    """
                    SELECT version_sha256 FROM discovery_hypothesis_versions
                    WHERE hypothesis_id = ? AND version = ?
                    """,
                    (str(card.id), current_version),
                ).fetchone()["version_sha256"]
            version = current_version + 1
            version_hash = content_hash(
                {
                    "hypothesis_id": str(card.id),
                    "version": version,
                    "content": content,
                    "parent": parent,
                }
            )
            if row is None:
                connection.execute(
                    """
                    INSERT INTO discovery_hypotheses(
                        id, status, current_version, created_at, updated_at
                    ) VALUES (?, 'draft', 0, ?, ?)
                    """,
                    (str(card.id), timestamp, timestamp),
                )
            connection.execute(
                """
                INSERT INTO discovery_hypothesis_versions(
                    hypothesis_id, version, content_json, question_sha256,
                    corpus_sha256, evidence_sha256, model_sha256, prompt_sha256,
                    parent_version_sha256, version_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(card.id),
                    version,
                    json.dumps(content, ensure_ascii=False, sort_keys=True),
                    card.question_sha256,
                    card.corpus_sha256,
                    card.evidence_sha256,
                    card.model_sha256,
                    card.prompt_sha256,
                    parent,
                    version_hash,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE discovery_hypotheses
                SET current_version = ?, status = 'draft', updated_at = ?
                WHERE id = ?
                """,
                (version, timestamp, str(card.id)),
            )
        return version, version_hash

    def review(
        self,
        hypothesis_id: UUID,
        version: int,
        review: HumanHypothesisReview,
    ) -> UUID:
        review_id = uuid4()
        status = "retained" if review.decision == "retain" else "rejected"
        with self.database.transaction() as connection:
            current = connection.execute(
                """
                SELECT current_version FROM discovery_hypotheses WHERE id = ?
                """,
                (str(hypothesis_id),),
            ).fetchone()
            if current is None or int(current["current_version"]) != version:
                raise ValueError("only the current immutable hypothesis version can be reviewed")
            connection.execute(
                """
                INSERT INTO discovery_hypothesis_reviews(
                    id, hypothesis_id, version, decision, expert_reference, comment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(review_id),
                    str(hypothesis_id),
                    version,
                    review.decision,
                    review.expert_reference,
                    review.comment,
                    review.created_at.astimezone(UTC).isoformat(),
                ),
            )
            connection.execute(
                "UPDATE discovery_hypotheses SET status = ?, updated_at = ? WHERE id = ?",
                (status, review.created_at.astimezone(UTC).isoformat(), str(hypothesis_id)),
            )
        return review_id

    def status(self, hypothesis_id: UUID) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM discovery_hypotheses WHERE id = ?",
                (str(hypothesis_id),),
            ).fetchone()
        return str(row["status"]) if row else None

    def persist_comparison(
        self,
        comparison: PairwiseComparison,
        *,
        rubric_version: str,
        created_at: datetime | None = None,
    ) -> UUID:
        comparison_id = uuid4()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO discovery_pairwise_comparisons(
                    id, left_hypothesis_id, right_hypothesis_id, winner_hypothesis_id,
                    judge_reference, rubric_version, left_presented_first, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(comparison_id),
                    comparison.left_id,
                    comparison.right_id,
                    comparison.winner_id,
                    comparison.judge_reference,
                    rubric_version,
                    int(comparison.left_presented_first),
                    (created_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
                ),
            )
        return comparison_id

    def persist_ranking(
        self,
        ranking: list[BTLRank],
        comparisons: list[PairwiseComparison],
        *,
        seed: int,
        created_at: datetime | None = None,
    ) -> UUID:
        ranking_id = uuid4()
        timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO discovery_ranking_snapshots(
                    id, comparison_sha256, ranking_json, seed, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(ranking_id),
                    content_hash([item.model_dump() for item in comparisons]),
                    json.dumps([item.model_dump() for item in ranking], sort_keys=True),
                    seed,
                    timestamp,
                ),
            )
        return ranking_id

    def persist_analysis(self, record: AnalysisRecord) -> UUID:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO discovery_analysis_records(
                    id, dataset_id, record_json, input_sha256, output_sha256,
                    approved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.id),
                    str(record.dataset_id),
                    record.model_dump_json(),
                    record.input_sha256,
                    record.output_sha256,
                    record.approved_by,
                    record.created_at.astimezone(UTC).isoformat(),
                ),
            )
        return record.id

    def record_cycle_approval(self, approval: DiscoveryCycleApproval) -> UUID:
        approval_id = uuid4()
        with self.database.transaction() as connection:
            hypothesis = connection.execute(
                "SELECT status FROM discovery_hypotheses WHERE id = ?",
                (str(approval.previous_hypothesis_id),),
            ).fetchone()
            if hypothesis is None or hypothesis["status"] != "retained":
                raise ValueError("a discovery cycle requires a retained previous hypothesis")
            connection.execute(
                """
                INSERT INTO discovery_cycle_approvals(
                    id, previous_hypothesis_id, analysis_id, next_hypothesis_id,
                    decision, expert_reference, provenance_json, comment, created_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    str(approval_id),
                    str(approval.previous_hypothesis_id),
                    str(approval.analysis_id),
                    approval.decision,
                    approval.expert_reference,
                    json.dumps(
                        {
                            "literature_evidence_ids": approval.literature_evidence_ids,
                            "experimental_dataset_ids": [
                                str(item) for item in approval.experimental_dataset_ids
                            ],
                        },
                        sort_keys=True,
                    ),
                    approval.comment,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return approval_id

    def require_next_cycle_approval(
        self,
        previous_hypothesis_id: UUID,
        analysis_id: UUID,
    ) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM discovery_cycle_approvals
                WHERE previous_hypothesis_id = ? AND analysis_id = ?
                  AND decision = 'approve_next'
                LIMIT 1
                """,
                (str(previous_hypothesis_id), str(analysis_id)),
            ).fetchone()
        if row is None:
            raise PermissionError("the next discovery cycle requires explicit human approval")
