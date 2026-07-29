"""Explicitly download and save one embedding model under data/models."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import load_settings
from app.desktop.model_integrity import write_model_manifest
from app.ingestion.embeddings import local_model_path


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
    args = build_parser().parse_args(argv)
    if not args.allow_network:
        print(
            "Refusé : ajoutez --allow-network pour autoriser explicitement le téléchargement "
            "du modèle. Aucun fichier du corpus ne sera lu.",
            file=sys.stderr,
        )
        return 2

    settings = load_settings(args.config)
    model_name = args.model or settings.embeddings.model_name
    destination = local_model_path(settings, model_name).resolve()
    if destination.exists():
        print(f"Modèle local déjà présent : {destination}")
        return 0

    settings.paths.models_dir.mkdir(parents=True, exist_ok=True)
    try:
        import sentence_transformers
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - installation concern
        raise RuntimeError(
            "Installez d'abord sentence-transformers depuis requirements.txt"
        ) from exc

    print(f"Téléchargement explicite du modèle public : {model_name}")
    print("Le script n'ouvre ni SQLite, ni data/pdf, ni data/extracted.")
    with TemporaryDirectory(prefix=".model-download-", dir=settings.paths.models_dir) as temp:
        temporary_root = Path(temp)
        model = SentenceTransformer(
            model_name,
            device="cpu",
            cache_folder=str(temporary_root / "registry-cache"),
            trust_remote_code=False,
            local_files_only=False,
        )
        model.max_seq_length = settings.embeddings.max_sequence_length
        prepared = temporary_root / "prepared-model"
        model.save_pretrained(str(prepared), safe_serialization=True)
        metadata = {
            "source_model": model_name,
            "prepared_at": datetime.now(UTC).isoformat(),
            "sentence_transformers_version": sentence_transformers.__version__,
            "max_sequence_length": settings.embeddings.max_sequence_length,
            "trust_remote_code": False,
        }
        (prepared / "local_science_rag_source.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_model_manifest(prepared, model_name)
        del model
        prepared.replace(destination)

    print(f"Modèle local prêt : {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
