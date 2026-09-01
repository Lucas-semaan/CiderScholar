"""Build local reranker training JSONL from CiderQA development and A-D judgments."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.database.sqlite import Database
from app.evaluation.ciderqa import load_split_for_purpose
from app.retrieval.specialized_training import (
    build_specialized_reranker_dataset,
    ciderqa_training_examples,
    load_semantic_grade_judgments,
    semantic_grade_training_examples,
    write_specialized_reranker_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--grade-decisions", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    development = load_split_for_purpose(
        arguments.manifest,
        "development",
        purpose="development",
    )
    ciderqa_examples = ciderqa_training_examples(development)
    grade_examples = []
    if arguments.grade_decisions is not None:
        if arguments.database is None:
            raise ValueError("--database is required with --grade-decisions")
        database = Database(arguments.database)
        grade_examples = semantic_grade_training_examples(
            database,
            load_semantic_grade_judgments(arguments.grade_decisions),
        )
    dataset = build_specialized_reranker_dataset(ciderqa_examples, grade_examples)
    destination = write_specialized_reranker_dataset(dataset, arguments.output)
    print(f"dataset={destination}")
    print(f"examples={len(dataset.examples)}")
    print(f"ciderqa={sum(item.source == 'ciderqa' for item in dataset.examples)}")
    print(f"semantic_a_d={sum(item.source == 'semantic_a_d' for item in dataset.examples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
