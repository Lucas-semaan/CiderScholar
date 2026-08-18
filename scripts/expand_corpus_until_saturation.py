"""Expand every cider theme until sources saturate, defer, or the time budget expires."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.updates.harvest import CIDER_BULK_QUERY_WAVES, BulkHarvestReport, CiderBulkHarvester
from app.updates.harvest_closures import (
    WEEKLY_CLOSURE_DAYS,
    WeeklyHarvestClosureRegistry,
    weekly_harvest_closure_path,
)
from app.updates.harvest_queries import (
    CIDER_EXPANDED_QUERY_WAVES,
    CIDER_MATERIAL_QUERY_WAVES,
    CIDER_MICROBIOLOGY_QUERY_WAVES,
    CIDER_SPECIALIZED_QUERY_WAVES,
)
from app.updates.service import CLIENTS

QUERY_SETS = {
    "focused": CIDER_BULK_QUERY_WAVES,
    "expanded": CIDER_EXPANDED_QUERY_WAVES,
    "specialized": CIDER_SPECIALIZED_QUERY_WAVES,
    "materials": CIDER_MATERIAL_QUERY_WAVES,
    "microbiology": CIDER_MICROBIOLOGY_QUERY_WAVES,
}
PROFILE_STATES = {"active", "saturated", "closed_weekly", "limited", "complete"}
RETRY_AT_PATTERN = re.compile(r"retry_at=([^;\s]+)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=tuple(CLIENTS),
        default=list(CLIENTS),
    )
    parser.add_argument(
        "--query-sets",
        nargs="+",
        choices=tuple(QUERY_SETS),
        default=list(QUERY_SETS),
    )
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-runs-per-profile", type=int, default=1)
    parser.add_argument("--timeout-hours", type=float, default=12.0)
    parser.add_argument(
        "--reset-deadline",
        action="store_true",
        help=(
            "Start a new explicit timeout window while preserving profiles and offsets "
            "from the existing checkpoint"
        ),
    )
    parser.add_argument(
        "--wait-for-retries",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wait for provider retry windows that occur before the campaign deadline",
    )
    parser.add_argument("--run-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not 1 <= arguments.page_size <= 50:
        raise ValueError("page-size must be between 1 and 50")
    if not 1 <= arguments.max_runs_per_profile <= 100:
        raise ValueError("max-runs-per-profile must be between 1 and 100")
    if not 0.1 <= arguments.timeout_hours <= 168:
        raise ValueError("timeout-hours must be between 0.1 and 168")

    settings = settings_for_corpus(load_settings(arguments.config), CorpusScope.COMMON)
    profile = load_local_profile()
    if profile is LocalProfile.ADMIN:
        AdminBibliographicKeyVault(settings, profile).hydrate_process_environment()
    settings.harvest.enabled = True
    settings.paths.create()
    database = Database(settings.paths.database_path)
    database.initialize()
    run_dir = (
        arguments.run_dir or settings.paths.exports_dir / "corpus-expansion-all-themes-20260812"
    ).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint = _load_checkpoint(checkpoint_path)
    started_at = datetime.now(UTC)
    if arguments.reset_deadline:
        previous_deadline = checkpoint.get("deadline")
        checkpoint.setdefault("deadline_history", []).append(
            {
                "deadline": previous_deadline,
                "resumed_at": started_at.isoformat(),
                "reason": "explicit_reset_deadline",
            }
        )
        deadline = started_at + timedelta(hours=arguments.timeout_hours)
    else:
        deadline = _campaign_deadline(checkpoint, started_at, arguments.timeout_hours)
    checkpoint.setdefault("started_at", started_at.isoformat())
    checkpoint["last_resumed_at"] = started_at.isoformat()
    checkpoint["deadline"] = deadline.isoformat()
    checkpoint["database_path"] = str(database.path.resolve())
    checkpoint.setdefault("profiles", {})
    _write_json(checkpoint_path, checkpoint)

    source_states = checkpoint.setdefault("source_states", {})
    if not isinstance(source_states, dict):
        raise ValueError("corpus expansion checkpoint source states are invalid")
    selected_sources = tuple(dict.fromkeys(arguments.sources))
    selected_query_sets = tuple(dict.fromkeys(arguments.query_sets))
    weekly_closures = WeeklyHarvestClosureRegistry(
        weekly_harvest_closure_path(settings.paths.common_dir)
    )
    while datetime.now(UTC) < deadline:
        active_sources = False
        for source in selected_sources:
            if datetime.now(UTC) >= deadline:
                break
            previous_source_state = str(source_states.get(source) or "")
            if _source_checkpoint_is_blocked(previous_source_state, datetime.now(UTC)):
                print(
                    f"source={source} state={previous_source_state} checkpoint=skipped",
                    flush=True,
                )
                continue
            if not _source_available(settings, source):
                source_states[source] = "unavailable_missing_credential"
                _write_json(checkpoint_path, checkpoint)
                print(f"source={source} state=unavailable_missing_credential", flush=True)
                continue
            source_state = _run_source(
                settings=settings,
                database=database,
                source=source,
                query_sets=selected_query_sets,
                page_size=arguments.page_size,
                max_runs=arguments.max_runs_per_profile,
                deadline=deadline,
                checkpoint=checkpoint,
                checkpoint_path=checkpoint_path,
                weekly_closures=weekly_closures,
            )
            source_states[source] = source_state
            active_sources = active_sources or source_state == "active"
            _write_json(checkpoint_path, checkpoint)

        if active_sources and datetime.now(UTC) < deadline:
            continue

        retry_at = _next_checkpoint_retry(checkpoint.get("profiles"), datetime.now(UTC))
        checkpoint["next_retry_at"] = retry_at.isoformat() if retry_at else None
        _write_json(checkpoint_path, checkpoint)
        if (
            not arguments.wait_for_retries
            or retry_at is None
            or retry_at > deadline
            or datetime.now(UTC) >= deadline
        ):
            break
        print(f"campaign_waiting_until={retry_at.isoformat()}", flush=True)
        while datetime.now(UTC) < min(retry_at, deadline):
            remaining = (min(retry_at, deadline) - datetime.now(UTC)).total_seconds()
            time.sleep(max(0.1, min(60.0, remaining)))

    finished_at = datetime.now(UTC)
    timed_out = finished_at >= deadline
    checkpoint["finished_at"] = finished_at.isoformat()
    checkpoint["timed_out"] = timed_out
    checkpoint["source_states"] = source_states
    checkpoint["state"] = "timeout" if timed_out else "sources_processed"
    _write_json(checkpoint_path, checkpoint)
    print(
        f"campaign_state={checkpoint['state']} checkpoint={checkpoint_path} "
        f"sources={json.dumps(source_states, ensure_ascii=False)}",
        flush=True,
    )
    return 0


def _run_source(
    *,
    settings: Any,
    database: Database,
    source: str,
    query_sets: tuple[str, ...],
    page_size: int,
    max_runs: int,
    deadline: datetime,
    checkpoint: dict[str, Any],
    checkpoint_path: Path,
    weekly_closures: WeeklyHarvestClosureRegistry,
) -> str:
    profiles = checkpoint["profiles"]
    has_active_profile = False
    for query_set in query_sets:
        if datetime.now(UTC) >= deadline:
            break
        profile = f"expand_{query_set}_{source}_v1"
        previous = profiles.get(profile, {})
        now = datetime.now(UTC)
        closure = weekly_closures.active(profile, now=now)
        if closure is None and _legacy_no_gain_profile(previous):
            closed_at = _profile_updated_at(previous, fallback=now)
            if closed_at + timedelta(days=WEEKLY_CLOSURE_DAYS) > now:
                closure = weekly_closures.close_weekly(
                    profile=profile,
                    source=source,
                    query_set=query_set,
                    reason="legacy saturated checkpoint migrated to weekly no-gain closure",
                    consecutive_no_gain_runs=max(
                        1,
                        int(previous.get("consecutive_no_gain_runs") or 1),
                    ),
                    closed_at=closed_at,
                )
        if closure is not None:
            profiles[profile] = {
                **(previous if isinstance(previous, dict) else {}),
                "source": source,
                "query_set": query_set,
                "state": "closed_weekly",
                "retry_at": closure["closed_until"],
                "updated_at": closure["closed_at"],
                "closure_reason": closure["reason"],
            }
            _write_json(checkpoint_path, checkpoint)
            print(
                f"source={source} query_set={query_set} profile={profile} "
                f"state=closed_weekly until={closure['closed_until']}",
                flush=True,
            )
            continue
        if isinstance(previous, dict) and previous.get("state") in {
            "closed_weekly",
            "saturated",
        }:
            previous = {**previous, "state": "active", "retry_at": None}
        if _profile_checkpoint_is_terminal(previous, datetime.now(UTC)):
            continue
        print(f"source={source} query_set={query_set} profile={profile}", flush=True)
        report = CiderBulkHarvester(settings, database).run(
            target_new_accepted_abstracts=10_000,
            page_size=page_size,
            max_runs=max_runs,
            profile=profile,
            query_waves=QUERY_SETS[query_set],
            sources=(source,),
            progress=lambda message: print(message, flush=True),
        )
        state, retry_at = _profile_state(report, len(QUERY_SETS[query_set]))
        consecutive_no_gain_runs = _next_no_gain_count(previous, report)
        consecutive_error_runs = _next_error_count(previous, report)
        if state == "active" and consecutive_error_runs >= 2:
            state = "limited"
        if state == "active" and _weekly_closure_due(
            consecutive_no_gain_runs,
            wave_count=len(QUERY_SETS[query_set]),
        ):
            closure = weekly_closures.close_weekly(
                profile=profile,
                source=source,
                query_set=query_set,
                reason=(
                    "no new accepted abstract after two complete thematic rotations "
                    f"for query family {query_set}"
                ),
                consecutive_no_gain_runs=consecutive_no_gain_runs,
            )
            state = "closed_weekly"
            retry_at = str(closure["closed_until"])
        elif report.new_accepted_abstracts > 0:
            weekly_closures.clear(profile)
        profiles[profile] = {
            "source": source,
            "query_set": query_set,
            "state": state,
            "retry_at": retry_at,
            "consecutive_no_gain_runs": consecutive_no_gain_runs,
            "consecutive_error_runs": consecutive_error_runs,
            "updated_at": datetime.now(UTC).isoformat(),
            "report": report.model_dump(mode="json"),
        }
        _write_json(checkpoint_path, checkpoint)
        print(
            f"profile={profile} state={state} "
            f"new_accepted_abstracts={report.new_accepted_abstracts} "
            f"stop_reason={report.stop_reason} no_gain_runs={consecutive_no_gain_runs} "
            f"error_runs={consecutive_error_runs} retry_at={retry_at or '-'}",
            flush=True,
        )
        if state == "active":
            has_active_profile = True
        elif state == "limited":
            return f"limited_until_{retry_at}" if retry_at else "limited"
    if has_active_profile and datetime.now(UTC) < deadline:
        return "active"
    return "timeout" if datetime.now(UTC) >= deadline else "processed"


def _profile_state(
    report: BulkHarvestReport,
    wave_count: int,
) -> tuple[Literal["active", "saturated", "limited", "complete"], str | None]:
    if report.target_reached:
        return "complete", None
    tail = report.harvest_runs[-min(len(report.harvest_runs), wave_count * 2) :]
    terminal_runs = report.harvest_runs[-2:]
    errorful_tail = len(terminal_runs) == 2 and all(run.errors for run in terminal_runs)
    retry_at = _retry_at_for_messages(
        error.get("message", "")
        for run in (terminal_runs if errorful_tail else tail)
        for error in run.errors
    )
    if errorful_tail:
        return "limited", retry_at
    if report.stop_reason == "no_progress":
        return "saturated", None
    return "active", retry_at


def _next_no_gain_count(previous: Any, report: BulkHarvestReport) -> int:
    if report.new_accepted_abstracts > 0:
        return 0
    prior = previous.get("consecutive_no_gain_runs", 0) if isinstance(previous, dict) else 0
    try:
        normalized_prior = max(0, int(prior))
    except (TypeError, ValueError):
        normalized_prior = 0
    if _report_is_completely_errorful(report):
        return normalized_prior
    return normalized_prior + max(1, len(report.harvest_runs))


def _next_error_count(previous: Any, report: BulkHarvestReport) -> int:
    if not _report_is_completely_errorful(report):
        return 0
    prior = previous.get("consecutive_error_runs", 0) if isinstance(previous, dict) else 0
    try:
        normalized_prior = max(0, int(prior))
    except (TypeError, ValueError):
        normalized_prior = 0
    return normalized_prior + max(1, len(report.harvest_runs))


def _weekly_closure_due(consecutive_no_gain_runs: int, *, wave_count: int) -> bool:
    if wave_count < 1:
        raise ValueError("weekly closure wave count must be positive")
    return consecutive_no_gain_runs >= wave_count * 2


def _report_is_completely_errorful(report: BulkHarvestReport) -> bool:
    return bool(report.harvest_runs) and all(
        run.errors and getattr(run, "raw_record_count", 0) == 0 for run in report.harvest_runs
    )


def _latest_retry_at(messages: Any) -> str | None:
    parsed: list[datetime] = []
    for message in messages:
        match = RETRY_AT_PATTERN.search(str(message))
        if match is None:
            continue
        try:
            parsed.append(datetime.fromisoformat(match.group(1)).astimezone(UTC))
        except ValueError:
            continue
    return max(parsed).isoformat() if parsed else None


def _retry_at_for_messages(messages: Any, *, now: datetime | None = None) -> str | None:
    values = [str(message) for message in messages]
    explicit = _latest_retry_at(values)
    if explicit is not None:
        return explicit
    current = now or datetime.now(UTC)
    folded = " ".join(values).casefold()
    if "daily budget" in folded:
        tomorrow = (current + timedelta(days=1)).date()
        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=UTC).isoformat()
    if "429" in folded or "rate limit" in folded:
        return (current + timedelta(hours=1)).isoformat()
    if "weekly" in folded or "weekly quota" in folded:
        return (current + timedelta(days=7)).isoformat()
    return None


def _profile_checkpoint_is_terminal(profile: Any, now: datetime) -> bool:
    if not isinstance(profile, dict):
        return False
    state = profile.get("state")
    if state == "complete":
        return True
    if state == "closed_weekly":
        retry_at = profile.get("retry_at")
        if not retry_at:
            return True
        try:
            return now < datetime.fromisoformat(str(retry_at)).astimezone(UTC)
        except ValueError:
            return True
    terminal_messages = _checkpoint_terminal_error_messages(profile)
    if state == "saturated" and not terminal_messages:
        return True
    if state not in {"saturated", "limited"}:
        return False
    retry_at = profile.get("retry_at")
    if not retry_at:
        observed_at = now
        with suppress(ValueError):
            observed_at = datetime.fromisoformat(str(profile.get("updated_at"))).astimezone(UTC)
        retry_at = _retry_at_for_messages(terminal_messages, now=observed_at)
    if not retry_at:
        return True
    try:
        return now < datetime.fromisoformat(str(retry_at)).astimezone(UTC)
    except ValueError:
        return True


def _checkpoint_terminal_error_messages(profile: dict[str, Any]) -> list[str]:
    report = profile.get("report")
    runs = report.get("harvest_runs", []) if isinstance(report, dict) else []
    tail = runs[-2:] if isinstance(runs, list) else []
    persisted_error_streak = int(profile.get("consecutive_error_runs") or 0)
    has_terminal_errors = len(tail) == 2 and all(
        isinstance(run, dict) and run.get("errors") for run in tail
    )
    if not has_terminal_errors and persisted_error_streak >= 2 and tail:
        tail = tail[-1:]
        has_terminal_errors = bool(isinstance(tail[0], dict) and tail[0].get("errors"))
    if not has_terminal_errors:
        return []
    return [
        str(error.get("message", ""))
        for run in tail
        for error in run.get("errors", [])
        if isinstance(error, dict)
    ]


def _source_checkpoint_is_blocked(state: str, now: datetime) -> bool:
    prefix = "limited_until_"
    if not state.startswith(prefix):
        return False
    try:
        return now < datetime.fromisoformat(state[len(prefix) :]).astimezone(UTC)
    except ValueError:
        return True


def _next_checkpoint_retry(profiles: Any, now: datetime) -> datetime | None:
    if not isinstance(profiles, dict):
        return None
    candidates: list[datetime] = []
    for profile in profiles.values():
        if not isinstance(profile, dict) or profile.get("state") not in {
            "limited",
            "saturated",
            "closed_weekly",
        }:
            continue
        terminal_messages = _checkpoint_terminal_error_messages(profile)
        if profile.get("state") == "saturated" and not terminal_messages:
            continue
        retry_at = profile.get("retry_at")
        if not retry_at:
            observed_at = now
            with suppress(ValueError):
                observed_at = datetime.fromisoformat(str(profile.get("updated_at"))).astimezone(UTC)
            retry_at = _retry_at_for_messages(terminal_messages, now=observed_at)
        if not retry_at:
            continue
        with suppress(ValueError):
            parsed = datetime.fromisoformat(str(retry_at)).astimezone(UTC)
            if parsed > now:
                candidates.append(parsed)
    return min(candidates) if candidates else None


def _legacy_no_gain_profile(profile: Any) -> bool:
    return (
        isinstance(profile, dict)
        and profile.get("state") == "saturated"
        and not _checkpoint_terminal_error_messages(profile)
    )


def _profile_updated_at(profile: Any, *, fallback: datetime) -> datetime:
    if not isinstance(profile, dict):
        return fallback
    try:
        value = datetime.fromisoformat(str(profile.get("updated_at")))
    except ValueError:
        return fallback
    return value.astimezone(UTC) if value.tzinfo is not None else fallback


def _source_available(settings: Any, source: str) -> bool:
    client = CLIENTS[source](settings)
    try:
        return client.is_available()
    finally:
        client.close()


def _load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "profiles": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("corpus expansion checkpoint is invalid")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("corpus expansion checkpoint profiles are invalid")
    for profile in profiles.values():
        if not isinstance(profile, dict) or profile.get("state") not in PROFILE_STATES:
            raise ValueError("corpus expansion checkpoint profile state is invalid")
    return payload


def _campaign_deadline(
    checkpoint: dict[str, Any], resumed_at: datetime, timeout_hours: float
) -> datetime:
    stored = checkpoint.get("deadline")
    if stored:
        with suppress(ValueError):
            parsed = datetime.fromisoformat(str(stored))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
    return resumed_at + timedelta(hours=timeout_hours)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
