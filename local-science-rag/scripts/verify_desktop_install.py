"""Fail installation when the packaged Windows host, config or E5 payload is invalid."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.config import load_settings
from app.desktop.layout import create_desktop_layout, desktop_paths
from app.desktop.model_integrity import verify_model_manifest
from app.desktop.system_checks import validate_windows_11_x64
from app.ingestion.embeddings import local_model_path
from app.retrieval.reranker import local_reranker_model_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    os.environ["CIDERSCHOLAR_CONFIG_PATH"] = str(arguments.config.resolve())
    validate_windows_11_x64()
    paths = desktop_paths()
    create_desktop_layout(paths)
    settings = load_settings(arguments.config)
    verify_model_manifest(local_model_path(settings), settings.embeddings.model_name)
    verify_model_manifest(
        local_reranker_model_path(settings),
        settings.reranker.model_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
