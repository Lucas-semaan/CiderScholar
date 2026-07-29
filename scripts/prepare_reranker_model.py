"""Explicitly download, freeze and fingerprint the configured cross-encoder."""

from __future__ import annotations

import argparse
import gc
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import load_settings
from app.desktop.model_integrity import verify_model_manifest, write_model_manifest
from app.ingestion.embeddings import model_storage_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument("--model", help="Registry model name; defaults to configuration")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Required acknowledgement for this model-only network operation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.allow_network:
        print(
            "Refusé : ajoutez --allow-network pour autoriser explicitement le téléchargement "
            "du cross-encoder. Aucun corpus n’est lu.",
            file=sys.stderr,
        )
        return 2

    settings = load_settings(arguments.config)
    model_name = arguments.model or settings.reranker.model_name
    destination = (settings.paths.models_dir / model_storage_name(model_name)).resolve()
    if destination.exists():
        verify_model_manifest(destination, model_name)
        print(f"Cross-encoder local déjà vérifié : {destination}")
        return 0

    settings.paths.models_dir.mkdir(parents=True, exist_ok=True)
    try:
        import sentence_transformers
        from sentence_transformers import CrossEncoder
    except ImportError as error:  # pragma: no cover - installation concern
        raise RuntimeError(
            "Installez d’abord sentence-transformers depuis requirements.txt"
        ) from error

    print(f"Téléchargement explicite du modèle public : {model_name}")
    print("Le script n’ouvre ni SQLite, ni data/pdf, ni data/extracted.")
    with TemporaryDirectory(prefix=".reranker-download-", dir=settings.paths.models_dir) as temp:
        temporary_root = Path(temp)
        model = CrossEncoder(
            model_name,
            device="cpu",
            cache_folder=str(temporary_root / "registry-cache"),
            trust_remote_code=False,
            local_files_only=False,
        )
        prepared = temporary_root / "prepared-model"
        model.save_pretrained(
            str(prepared),
            create_model_card=True,
            safe_serialization=True,
        )
        metadata = {
            "source_model": model_name,
            "prepared_at": datetime.now(UTC).isoformat(),
            "sentence_transformers_version": sentence_transformers.__version__,
            "trust_remote_code": False,
        }
        (prepared / "ciderscholar-reranker-source.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_model_manifest(prepared, model_name)
        del model
        gc.collect()
        prepared.replace(destination)

    verify_model_manifest(destination, model_name)
    print(f"Cross-encoder local prêt : {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
