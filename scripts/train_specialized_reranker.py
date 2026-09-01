"""Continue training a local reranker candidate without activating it automatically."""

from __future__ import annotations

import argparse
import gc
import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import load_settings
from app.desktop.model_integrity import verify_model_manifest, write_model_manifest
from app.ingestion.embeddings import model_storage_name
from app.retrieval.specialized_training import load_specialized_reranker_dataset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-model-name", required=True)
    parser.add_argument("--base-model-name")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not 1 <= arguments.epochs <= 20:
        raise ValueError("epochs must be between 1 and 20")
    if not 1 <= arguments.batch_size <= 64:
        raise ValueError("batch size must be between 1 and 64")
    dataset = load_specialized_reranker_dataset(arguments.dataset)
    if len(dataset.examples) < 20:
        raise ValueError("specialized reranker training requires at least twenty examples")
    labels = {example.grade for example in dataset.examples}
    if not labels.intersection({"A", "B"}) or not labels.intersection({"C", "D"}):
        raise ValueError("specialized training requires both positive and negative grades")

    settings = load_settings(arguments.config)
    base_name = arguments.base_model_name or settings.reranker.model_name
    base_path = settings.paths.models_dir / model_storage_name(base_name)
    verify_model_manifest(base_path, base_name)
    destination = (
        settings.paths.models_dir / model_storage_name(arguments.output_model_name)
    ).resolve()
    if destination.exists():
        raise FileExistsError("specialized reranker destination already exists")

    try:
        import sentence_transformers
        from sentence_transformers import CrossEncoder, InputExample
        from torch.utils.data import DataLoader
    except ImportError as exc:  # pragma: no cover - installation concern
        raise RuntimeError("reranker training dependencies are unavailable") from exc

    training_examples = [
        InputExample(
            texts=[example.query, example.document],
            label=example.target_score,
        )
        for example in dataset.examples
    ]
    dataloader = DataLoader(
        training_examples,
        shuffle=True,
        batch_size=arguments.batch_size,
    )
    settings.paths.models_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".specialized-reranker-",
        dir=settings.paths.models_dir,
    ) as temporary_name:
        prepared = Path(temporary_name) / "prepared-model"
        model = CrossEncoder(
            str(base_path),
            device=settings.reranker.device,
            local_files_only=True,
            trust_remote_code=False,
        )
        model.fit(
            train_dataloader=dataloader,
            epochs=arguments.epochs,
            warmup_steps=max(1, len(dataloader) // 10),
            show_progress_bar=True,
        )
        model.save_pretrained(
            str(prepared),
            create_model_card=True,
            safe_serialization=True,
        )
        metadata = {
            "model_name": arguments.output_model_name,
            "base_model": base_name,
            "trained_at": datetime.now(UTC).isoformat(),
            "sentence_transformers_version": sentence_transformers.__version__,
            "example_count": len(dataset.examples),
            "grade_counts": {
                grade: sum(example.grade == grade for example in dataset.examples)
                for grade in ("A", "B", "C", "D")
            },
            "epochs": arguments.epochs,
            "activation": "candidate_requires_ciderqa_promotion",
        }
        (prepared / "ciderscholar-specialized-training.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_model_manifest(prepared, arguments.output_model_name)
        del model
        gc.collect()
        prepared.replace(destination)
    verify_model_manifest(destination, arguments.output_model_name)
    print(f"candidate_model={destination}")
    print("activation=requires-ciderqa-promotion-and-explicit-config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
