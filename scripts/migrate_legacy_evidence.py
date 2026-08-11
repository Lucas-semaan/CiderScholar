"""Preview or migrate legacy persisted scientific evidence into the common corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.corpora import load_local_profile
from app.services.evidence_migration import EvidenceMigrationError, migrate_legacy_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a verified common SQLite snapshot, then apply the migration.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        report = migrate_legacy_evidence(
            load_settings(arguments.config),
            profile=load_local_profile(),
            apply=arguments.apply,
        )
    except EvidenceMigrationError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
