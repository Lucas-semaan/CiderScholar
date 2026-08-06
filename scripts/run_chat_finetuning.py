"""Run one sequential, resumable CiderScholar prompt-profile evaluation campaign."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.campaign import (
    CampaignExecutionError,
    EvaluationCampaignRunner,
    EvaluationCampaignSpec,
)
from app.jobs.repository import JobRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON manifest containing run_id and cells")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/database/science_rag.sqlite3"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--job-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--cancellation-grace-seconds", type=float, default=300.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    spec = EvaluationCampaignSpec.model_validate_json(
        arguments.manifest.read_text(encoding="utf-8")
    )
    try:
        result = EvaluationCampaignRunner(
            JobRepository(arguments.database),
            arguments.output,
            poll_seconds=arguments.poll_seconds,
            job_timeout_seconds=arguments.job_timeout_seconds,
            cancellation_grace_seconds=arguments.cancellation_grace_seconds,
        ).run(spec)
    except CampaignExecutionError as error:
        print(
            json.dumps(
                {"complete": False, "reliable": False, "error": str(error)},
                ensure_ascii=False,
            )
        )
        return 2
    print(result.model_dump_json(indent=2))
    return 0 if result.reliable else 1


if __name__ == "__main__":
    raise SystemExit(main())
