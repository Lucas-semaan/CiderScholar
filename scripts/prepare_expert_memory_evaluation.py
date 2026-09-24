"""Prepare two isolated, label-free campaigns from one expert-evaluation manifest."""

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
from app.evaluation.campaign import EvaluationCampaignPairCoordinator, EvaluationCampaignRunner
from app.evaluation.expert_memory import (
    ExpertEvaluationCase,
    ExpertEvaluationManifest,
    build_campaign_pair_specs,
    enqueue_evaluation_orchestrator_job,
)
from app.jobs.contracts import ACTIVE_JOB_STATES, JobState
from app.jobs.repository import JobRepository
from scripts.expert_cli_support import resolve_run_dir, write_json_atomic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--run-id-prefix", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--advance", action="store_true")
    action.add_argument("--advance-pair", action="store_true")
    action.add_argument("--enqueue-orchestrator", action="store_true")
    action.add_argument("--observe-job-id", type=UUID)
    parser.add_argument("--arm", choices=("base", "candidate"), default="base")
    parser.add_argument("--evaluation-id", type=UUID)
    parser.add_argument("--client-request-id", type=UUID)
    return parser


def _load_cases(path: Path) -> tuple[ExpertEvaluationCase, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("--cases must contain a JSON array")
    return tuple(ExpertEvaluationCase.model_validate(item) for item in raw)


def _campaign_is_terminal(path: Path, expected_cells: int) -> bool:
    if not path.is_file():
        return False
    state = json.loads(path.read_text(encoding="utf-8"))
    cells = state.get("cells")
    if not isinstance(cells, dict) or len(cells) != expected_cells:
        return False
    terminal = {JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.CANCELLED.value}
    return all(isinstance(cell, dict) and cell.get("state") in terminal for cell in cells.values())


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        settings = load_settings(arguments.config)
        run_dir = resolve_run_dir(settings, arguments.run_dir)
        manifest = ExpertEvaluationManifest.model_validate_json(
            arguments.manifest.read_text(encoding="utf-8")
        )
        cases = _load_cases(arguments.cases)
        database = Database(settings.paths.database_path)
        base, candidate = build_campaign_pair_specs(
            database,
            manifest,
            cases,
            run_id_prefix=arguments.run_id_prefix,
        )
        campaign_specs = {"base": base, "candidate": candidate}
        artifact = {
            "schema_version": 1,
            "manifest_sha256": manifest.manifest_sha256,
            "base": base.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
        }
        pair_path = run_dir / "campaign_pair.json"
        write_json_atomic(pair_path, artifact)
        action_result: dict[str, object] = {"status": "prepared"}
        if arguments.enqueue_orchestrator:
            if arguments.evaluation_id is None or arguments.client_request_id is None:
                raise ValueError(
                    "--enqueue-orchestrator requires --evaluation-id and --client-request-id"
                )
            job = enqueue_evaluation_orchestrator_job(
                database,
                evaluation_id=arguments.evaluation_id,
                campaign_pair_path=pair_path,
                client_request_id=arguments.client_request_id,
            )
            action_result = {
                "status": "orchestrator_enqueued",
                "job_id": str(job.id),
                "job_state": job.state.value,
            }
        elif arguments.advance_pair:
            repository = JobRepository(database.path)
            coordinator = EvaluationCampaignPairCoordinator(
                EvaluationCampaignRunner(repository, run_dir / "base", poll_seconds=0),
                EvaluationCampaignRunner(repository, run_dir / "candidate", poll_seconds=0),
            )
            action_result = {
                "status": "advanced_pair",
                **coordinator.advance_one(base, candidate).model_dump(mode="json"),
            }
        elif arguments.advance or arguments.observe_job_id is not None:
            if arguments.arm == "candidate" and not _campaign_is_terminal(
                run_dir / "base" / "state.json", len(base.cells)
            ):
                raise ValueError("base campaign must be terminal before candidate campaign")
            repository = JobRepository(database.path)
            spec = campaign_specs[arguments.arm]
            runner = EvaluationCampaignRunner(repository, run_dir / arguments.arm, poll_seconds=0)
            if arguments.advance:
                job = runner.advance_one(spec)
                action_result = {
                    "status": "advanced" if job is not None else "complete",
                    "arm": arguments.arm,
                    "job_id": str(job.id) if job is not None else None,
                    "job_state": job.state.value if job is not None else None,
                }
            else:
                observed = runner.observe_one(spec, arguments.observe_job_id)
                action_result = {
                    "status": "observed",
                    "arm": arguments.arm,
                    "job_id": str(observed.id),
                    "job_state": observed.state.value,
                    "job_active": observed.state in ACTIVE_JOB_STATES,
                }
        checkpoint = {
            "schema_version": 1,
            **action_result,
            "manifest_sha256": manifest.manifest_sha256,
            "base_run_id": base.run_id,
            "candidate_run_id": candidate.run_id,
        }
        write_json_atomic(
            run_dir / "manifest.json",
            {
                "schema_version": 1,
                "operation": "prepare_evaluation_campaign_pair",
                "manifest_sha256": manifest.manifest_sha256,
                "case_count": len(cases),
            },
        )
        write_json_atomic(run_dir / "checkpoint.json", checkpoint)
        write_json_atomic(run_dir / "report.json", artifact)
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
