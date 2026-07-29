"""Build a local expert-review file from CiderQA contextual snapshots."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.deep_research.retrieval import DeepResearchSearchSnapshot
from app.evaluation.ciderqa import load_ciderqa_manifest, load_split_for_purpose
from app.evaluation.contextual_adjudication import (
    build_contextual_adjudication,
    write_contextual_adjudication,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare local contextual adjudication without reading CiderQA answer labels."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--snapshots-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    manifest = load_ciderqa_manifest(arguments.manifest)
    dataset = load_split_for_purpose(
        arguments.manifest,
        "development",
        purpose="development",
    )
    root = arguments.snapshots_root.resolve()
    snapshots = [
        DeepResearchSearchSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("retrieval*.json"))
        if path.is_file() and not path.is_symlink()
    ]
    adjudication = build_contextual_adjudication(
        dataset,
        dataset_sha256=manifest.development.sha256,
        snapshots=snapshots,
    )
    written = write_contextual_adjudication(adjudication, arguments.output)
    print(f"adjudication={written}")
    print(f"items={len(adjudication.items)}")
    print("expert_labels_copied=0")
    print("network_calls=0")


if __name__ == "__main__":
    main()
