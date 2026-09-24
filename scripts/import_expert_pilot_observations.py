"""Validate or persist text-free human observations for one shadow pilot."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.config import load_settings
from app.database.sqlite import Database
from app.evaluation.expert_pilot import ExpertPilotObservation
from app.evaluation.expert_pilot_store import (
    ExpertPilotStoreError,
    get_pilot,
    record_pilot_observation,
)
from app.expert_feedback.models import ExpertPilotObservationCreate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pilot-id", type=UUID, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist validated observations; without this flag the command is read-only",
    )
    args = parser.parse_args(argv)

    try:
        payload = _load_payload(args.input, args.pilot_id)
        settings = load_settings(args.config)
        database = Database(settings.paths.database_path)
        pilot = get_pilot(database, args.pilot_id)
        if pilot is None:
            raise ExpertPilotStoreError("pilot_not_found")
        protocol_sha256 = str(pilot["plan"]["measurement_protocol_sha256"])
        observations = tuple(
            _build_observation(item, args.pilot_id, protocol_sha256) for item in payload
        )
        result: dict[str, object] = {
            "pilot_id": str(args.pilot_id),
            "observation_count": len(observations),
            "applied": False,
            "created_count": 0,
            "replayed_count": 0,
        }
        if args.apply:
            last = None
            created_count = 0
            for observation in observations:
                last, created = record_pilot_observation(database, args.pilot_id, observation)
                created_count += int(created)
            result.update(
                applied=True,
                created_count=created_count,
                replayed_count=len(observations) - created_count,
                metrics=last["metrics"] if last is not None else pilot["metrics"],
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except (
        OSError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
        sqlite3.Error,
        ExpertPilotStoreError,
    ) as error:
        print(
            json.dumps(
                {
                    "applied": False,
                    "code": str(error)
                    if isinstance(error, ExpertPilotStoreError)
                    else "invalid_input",
                    "error_type": type(error).__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1


def _load_payload(path: Path, pilot_id: UUID) -> tuple[ExpertPilotObservationCreate, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("pilot_id") != str(pilot_id):
        raise ValueError("input pilot_id does not match --pilot-id")
    observations = raw.get("observations")
    if not isinstance(observations, list):
        raise ValueError("input observations must be a list")
    parsed = tuple(ExpertPilotObservationCreate.model_validate(item) for item in observations)
    if len({item.case_sha256 for item in parsed}) != len(parsed):
        raise ValueError("input observations contain duplicate case_sha256 values")
    if len({item.observation_id for item in parsed}) != len(parsed):
        raise ValueError("input observations contain duplicate observation_id values")
    if len(parsed) > 20:
        raise ValueError("input contains more than 20 observations")
    return parsed


def _build_observation(
    item: ExpertPilotObservationCreate,
    pilot_id: UUID,
    protocol_sha256: str,
) -> ExpertPilotObservation:
    return ExpertPilotObservation(
        observation_id=item.observation_id,
        pilot_id=pilot_id,
        case_sha256=item.case_sha256,
        protocol_sha256=protocol_sha256,
        expert_time_seconds=item.expert_time_seconds,
        diagnosis_human_corrected=item.diagnosis_human_corrected,
        useful_effect=item.useful_effect,
        false_gain=item.false_gain,
        rollback_count=item.rollback_count,
        prompt_tokens=item.prompt_tokens,
        completion_tokens=item.completion_tokens,
        recorded_by=item.recorded_by,
    )


if __name__ == "__main__":
    raise SystemExit(main())
