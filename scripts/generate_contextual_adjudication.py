"""Generate a resumable local CiderQA contextual-adjudication package."""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.config import load_settings
from app.deep_research.contextual_summary import ContextualSummarizer
from app.deep_research.pipeline import build_deep_research_operations
from app.deep_research.retrieval import DeepResearchSearchSnapshot
from app.evaluation.ciderqa import load_ciderqa_manifest, load_split_for_purpose
from app.evaluation.contextual_adjudication import (
    build_contextual_adjudication,
    write_contextual_adjudication,
)
from app.jobs.contracts import DeepResearchPayload
from app.llm.argo_client import ArgoClient, ArgoLocalQuotaError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate contextual summaries for CiderQA development and write an unlabeled, "
            "local-only expert-review package."
        )
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question-limit", type=int)
    parser.add_argument("--summary-top-k", type=int, default=2)
    parser.add_argument(
        "--allow-argo",
        action="store_true",
        help="Required acknowledgement that this evaluation command may call ARGO.",
    )
    return parser.parse_args(argv)


def _payload(dataset_sha256: str, question_id: str, question: str) -> DeepResearchPayload:
    namespace = f"ciderqa-contextual:{dataset_sha256}:{question_id}"
    return DeepResearchPayload(
        message=question,
        conversation_id=uuid5(NAMESPACE_URL, f"{namespace}:conversation"),
        client_request_id=uuid5(NAMESPACE_URL, f"{namespace}:request"),
    )


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    if not arguments.allow_argo:
        raise SystemExit("--allow-argo is required because this command can call ARGO")
    if arguments.question_limit is not None and arguments.question_limit < 1:
        raise SystemExit("--question-limit must be positive")
    if not 1 <= arguments.summary_top_k <= 12:
        raise SystemExit("--summary-top-k must be between 1 and 12")

    settings = load_settings(arguments.config)
    manifest = load_ciderqa_manifest(arguments.manifest)
    dataset = load_split_for_purpose(
        arguments.manifest,
        "development",
        purpose="development",
    )
    questions = dataset.questions[: arguments.question_limit]
    client = ArgoClient(settings)
    summarizer = ContextualSummarizer(
        client,
        top_k=arguments.summary_top_k,
        relevance_threshold=0.5,
        strict_errors=True,
    )
    operations = build_deep_research_operations(
        settings,
        contextual_summarizer=summarizer,
        enable_response_cache=False,
    )
    snapshots: list[DeepResearchSearchSnapshot] = []
    quota_retry_at: str | None = None
    try:
        for question in questions:
            payload = _payload(manifest.development.sha256, question.id, question.question)
            if not operations.retrieval.exists(payload):
                operations.search(payload)
            snapshot = operations.retrieval.load(payload)
            if snapshot.contextual_evidence is None:
                try:
                    operations.extract_evidence(payload)
                except ArgoLocalQuotaError as error:
                    quota_retry_at = error.retry_at.isoformat()
                    break
                snapshot = operations.retrieval.load(payload)
            snapshots.append(snapshot)
    finally:
        operations.close()

    if not snapshots:
        if quota_retry_at:
            print(f"quota_retry_at={quota_retry_at}")
        print("completed_questions=0")
        return 3

    adjudication = build_contextual_adjudication(
        dataset,
        dataset_sha256=manifest.development.sha256,
        snapshots=snapshots,
    )
    written = write_contextual_adjudication(adjudication, arguments.output)
    print(f"adjudication={written}")
    print(f"completed_questions={len({item.question_id for item in adjudication.items})}")
    print(f"items={len(adjudication.items)}")
    print("ciderqa_answer_labels_read=0")
    if quota_retry_at:
        print(f"quota_retry_at={quota_retry_at}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
