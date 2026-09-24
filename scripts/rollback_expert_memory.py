"""Dry-run or explicitly roll back the active expert-memory candidate."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings
from app.corpora import LocalProfile, load_local_profile
from app.database.sqlite import Database
from app.expert_feedback.models import ExpertRollbackRequest
from app.expert_feedback.review import preflight_rollback, rollback_active_candidate
from scripts.expert_cli_support import resolve_output_path, write_json_atomic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", type=UUID, required=True)
    parser.add_argument("--expected-active-generation", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def _request(database: Database, release_id: UUID, generation: int, reason: str):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT package_sha256 FROM expert_releases WHERE id = ?",
            (str(release_id),),
        ).fetchone()
    if row is None:
        raise ValueError("rollback_target_not_found")
    return ExpertRollbackRequest(
        client_request_id=uuid5(
            NAMESPACE_URL,
            f"ciderscholar:expert-rollback:{release_id}:{generation}",
        ),
        target_release_id=release_id,
        target_release_sha256=row["package_sha256"],
        expected_active_generation=generation,
        reason=reason,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.expected_active_generation < 0:
            raise ValueError("--expected-active-generation must be non-negative")
        if load_local_profile() is not LocalProfile.ADMIN:
            raise PermissionError("administrator_profile_required")
        settings = load_settings(arguments.config)
        output = resolve_output_path(settings, arguments.output)
        database = Database(settings.paths.database_path)
        request = _request(
            database,
            arguments.release_id,
            arguments.expected_active_generation,
            arguments.reason,
        )
        result = (
            rollback_active_candidate(database, request)
            if arguments.apply
            else preflight_rollback(database, request)
        )
        report = {"applied": arguments.apply, **result}
        if output is not None:
            write_json_atomic(output, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except (OSError, PermissionError, ValueError, ValidationError, sqlite3.Error) as error:
        print(
            json.dumps(
                {"applied": False, "code": str(error), "error_type": type(error).__name__},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
