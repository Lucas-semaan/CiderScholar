from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import scripts.import_expert_pilot_observations as pilot_cli


def _observation_payload(pilot_id: UUID, *, case_sha256: str = "a" * 64) -> dict[str, object]:
    return {
        "pilot_id": str(pilot_id),
        "observations": [
            {
                "observation_id": str(uuid4()),
                "case_sha256": case_sha256,
                "expert_time_seconds": 42,
                "diagnosis_human_corrected": True,
                "useful_effect": "useful",
                "false_gain": False,
                "rollback_count": 0,
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "recorded_by": "expert-1",
            }
        ],
    }


def test_pilot_observation_cli_validates_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_id = uuid4()
    input_path = tmp_path / "observations.json"
    input_path.write_text(json.dumps(_observation_payload(pilot_id)), encoding="utf-8")
    monkeypatch.setattr(
        pilot_cli,
        "load_settings",
        lambda _path: SimpleNamespace(paths=SimpleNamespace(database_path=tmp_path / "db.sqlite3")),
    )
    monkeypatch.setattr(
        pilot_cli,
        "get_pilot",
        lambda _database, _pilot_id: {
            "plan": {"measurement_protocol_sha256": "b" * 64},
            "metrics": {"observation_count": 0},
        },
    )

    exit_code = pilot_cli.main(
        [
            "--input",
            str(input_path),
            "--pilot-id",
            str(pilot_id),
            "--config",
            str(tmp_path / "config.yaml"),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "applied": False,
        "created_count": 0,
        "observation_count": 1,
        "pilot_id": str(pilot_id),
        "replayed_count": 0,
    }
    assert not (tmp_path / "db.sqlite3").exists()


def test_pilot_observation_cli_rejects_duplicate_cases(tmp_path: Path) -> None:
    pilot_id = uuid4()
    payload = _observation_payload(pilot_id)
    duplicate = dict(payload["observations"][0])
    payload["observations"] = [payload["observations"][0], duplicate]
    input_path = tmp_path / "observations.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case_sha256"):
        pilot_cli._load_payload(input_path, pilot_id)


def test_pilot_observation_cli_apply_reports_created_and_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pilot_id = uuid4()
    input_path = tmp_path / "observations.json"
    input_path.write_text(json.dumps(_observation_payload(pilot_id)), encoding="utf-8")
    monkeypatch.setattr(
        pilot_cli,
        "load_settings",
        lambda _path: SimpleNamespace(paths=SimpleNamespace(database_path=tmp_path / "db.sqlite3")),
    )
    monkeypatch.setattr(
        pilot_cli,
        "get_pilot",
        lambda _database, _pilot_id: {
            "plan": {"measurement_protocol_sha256": "b" * 64},
            "metrics": {"observation_count": 0},
        },
    )
    calls: list[object] = []

    def fake_record(_database, _pilot_id, observation):
        calls.append(observation)
        return {"metrics": {"observation_count": len(calls)}}, len(calls) == 1

    monkeypatch.setattr(pilot_cli, "record_pilot_observation", fake_record)

    exit_code = pilot_cli.main(
        [
            "--input",
            str(input_path),
            "--pilot-id",
            str(pilot_id),
            "--config",
            str(tmp_path / "config.yaml"),
            "--apply",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["applied"] is True
    assert output["created_count"] == 1
    assert output["replayed_count"] == 0
    assert len(calls) == 1
    assert calls[0].protocol_sha256 == "b" * 64
