"""Merge the former split corpus into the single common corpus without deleting rollback data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.services.legacy_corpus_merge import merge_legacy_split_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    arguments = parser.parse_args(argv)
    settings = load_settings(arguments.config)
    report = merge_legacy_split_corpus(settings)
    report_path = settings.paths.exports_dir / "single-corpus-merge.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    print(f"Rapport : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
