"""Run or resume one deterministic diagnosis of a private expert correction."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.database.sqlite import Database
from app.services.expert_improvement import ExpertImprovementService
from scripts.expert_cli_support import resolve_run_dir, write_json_atomic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-id", type=UUID, required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--max-llm-requests", type=int, choices=(0,), default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.expected_revision < 1:
            raise ValueError("--expected-revision must be positive")
        settings = load_settings(arguments.config)
        run_dir = resolve_run_dir(settings, arguments.run_dir)
        database = Database(settings.paths.database_path)
        result, created = ExpertImprovementService(database).diagnose_correction(
            arguments.correction_id,
            expected_revision=arguments.expected_revision,
        )
        manifest = {
            "schema_version": 1,
            "operation": "diagnose",
            "correction_id": str(arguments.correction_id),
            "expected_revision": arguments.expected_revision,
            "max_llm_requests": arguments.max_llm_requests,
        }
        checkpoint = {
            "schema_version": 1,
            "status": "completed",
            "created": created,
            "diagnosis_id": str(result["id"]),
            "diagnosis_sha256": result["diagnosis_sha256"],
        }
        write_json_atomic(run_dir / "manifest.json", manifest)
        write_json_atomic(run_dir / "checkpoint.json", checkpoint)
        write_json_atomic(run_dir / "report.json", result)
        print(
            json.dumps({**checkpoint, "run_dir": str(run_dir)}, ensure_ascii=False, sort_keys=True)
        )
        return 0
    except (OSError, ValueError, ValidationError, sqlite3.Error) as error:
        print(
            json.dumps(
                {"completed": False, "code": "invalid_input", "error_type": type(error).__name__},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
