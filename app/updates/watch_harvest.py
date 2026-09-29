"""Fair, checkpointed recent and archive discovery using existing provider adapters."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from app.config import Settings
from app.updates.base import BibliographicApiDeferred, MissingBibliographicCredential
from app.updates.crossref import CrossrefClient
from app.updates.harvest import OPENALEX_SEARCH_COST_USD, BibliographicHarvestStore
from app.updates.harvest_closures import WeeklyHarvestClosureRegistry, weekly_harvest_closure_path
from app.updates.openalex import OpenAlexClient
from app.updates.service import CLIENTS
from app.updates.watch_content import ready_content
from app.updates.watch_models import WatchCheckpoint, WatchCursor, WatchTheme
from app.updates.watch_store import WatchStore

PAGE_SIZE = 25
MAX_NOTICES = 1000


def profile_key(source: str, theme: WatchTheme, lane: str) -> str:
    digest = hashlib.sha256(f"{theme.key}:{theme.query}:{lane}".encode()).hexdigest()[:24]
    return f"watch_{source}_{digest}"


class WatchHarvester:
    def __init__(self, settings: Settings, store: WatchStore) -> None:
        self.settings = settings.model_copy(deep=True)
        self.settings.harvest.profile = "bibliographic_watch"
        self.settings.harvest.per_source_limit = PAGE_SIZE
        self.settings.bibliographic.request_delay_seconds = max(
            settings.harvest.request_delay_seconds, settings.bibliographic.request_delay_seconds
        )
        self.store = store
        self.bibliography = BibliographicHarvestStore(store.database)
        self.closures = WeeklyHarvestClosureRegistry(
            weekly_harvest_closure_path(settings.paths.common_dir)
        )

    def run(self, checkpoint: WatchCheckpoint, boundary: Callable[[], bool]) -> None:
        """Continue from the saved provider cursor and commit each page with its checkpoint."""

        if not checkpoint.harvest_run_id:
            checkpoint.harvest_run_id, _ = self.bibliography.start_run(
                self.settings,
                themes={item.key: item.query for item in checkpoint.config.themes},
                sources=list(self.settings.bibliographic.sources),
            )
            self.store.save(checkpoint)
        pairs = [
            (source, theme)
            for theme in checkpoint.config.themes
            for source in self.settings.bibliographic.sources
        ]
        disabled: set[str] = set()
        finished: set[str] = set()
        recent_sources = {"crossref", "openalex"}
        spent = float(self.store.get(f"openalex_spent:{checkpoint.report.job_id}", 0))
        try:
            while pairs and checkpoint.report.examined < MAX_NOTICES and boundary():
                turn = int(self.store.get("turn", 0))
                preferred = "archive" if turn % 4 == 3 else "recent"
                choices = []
                for lane in (preferred, "archive" if preferred == "recent" else "recent"):
                    ordered = pairs[turn // 4 % len(pairs) :] + pairs[: turn // 4 % len(pairs)]
                    for source, theme in ordered:
                        if lane == "recent" and source not in recent_sources:
                            note = f"{source} : pagination sans filtre incrémental de date."
                            if note not in checkpoint.report.limitations:
                                checkpoint.report.limitations.append(note)
                            continue
                        key = profile_key(source, theme, lane)
                        if source in disabled or key in finished:
                            continue
                        cursor = WatchCursor.model_validate(self.store.get(key, {}))
                        now = datetime.now(UTC)
                        provider_retry = self.store.get(f"provider_retry:{source}")
                        if provider_retry and datetime.fromisoformat(provider_retry) > now:
                            disabled.add(source)
                            continue
                        if (cursor.retry_at and cursor.retry_at > now) or self.closures.active(key):
                            finished.add(key)
                            continue
                        choices.append((source, theme, lane, key, cursor))
                    if choices:
                        break
                if not choices:
                    break
                source, theme, lane, key, cursor = choices[0]
                if lane == "recent" and cursor.until is None:
                    cursor.since = cursor.since or (date.today() - timedelta(days=30)).isoformat()
                    cursor.until = date.today().isoformat()
                try:
                    with CLIENTS[source](self.settings) as client:
                        if isinstance(client, OpenAlexClient):
                            remaining = client.rate_limit_status().get("daily_remaining_usd")
                            cap = self.settings.harvest.openalex_max_cost_usd_per_run
                            if (
                                remaining is None
                                or float(remaining) < OPENALEX_SEARCH_COST_USD
                                or spent + OPENALEX_SEARCH_COST_USD > cap
                            ):
                                raise BibliographicApiDeferred(
                                    "free_budget", retry_at=datetime.now(UTC) + timedelta(days=1)
                                )
                            spent += OPENALEX_SEARCH_COST_USD
                            self.store.put(f"openalex_spent:{checkpoint.report.job_id}", spent)
                        if lane == "recent" and isinstance(
                            client, (CrossrefClient, OpenAlexClient)
                        ):
                            records = client.search(
                                theme.query,
                                PAGE_SIZE,
                                offset=cursor.offset,
                                since=date.fromisoformat(cursor.since),
                                until=date.fromisoformat(cursor.until),
                            )
                        else:
                            records = client.search(theme.query, PAGE_SIZE, offset=cursor.offset)
                except Exception as error:
                    # Record types only: HTTP exception messages can contain credential URLs.
                    code = type(error).__name__
                    if isinstance(error, BibliographicApiDeferred):
                        cursor.retry_at = error.retry_at
                        code = f"deferred_{error.status_code or 'quota'}"
                    elif isinstance(error, MissingBibliographicCredential):
                        code = "missing_credential"
                    else:
                        cursor.retry_at = datetime.now(UTC) + timedelta(hours=6)
                    checkpoint.report.errors.append(f"{source}/{theme.key}/{lane}: {code}")
                    disabled.add(source)
                    if cursor.retry_at:
                        self.store.put(f"provider_retry:{source}", cursor.retry_at.isoformat())
                    self.store.put(key, cursor.model_dump(mode="json"))
                    self.store.save(checkpoint)
                    continue
                self._persist_page(
                    checkpoint, records, source, theme, lane, key, cursor, turn, finished
                )
            checkpoint.harvest_done = True
        finally:
            self.bibliography.finish_run(
                run_id=checkpoint.harvest_run_id,
                state="partial"
                if checkpoint.report.errors or not checkpoint.harvest_done
                else "completed",
                raw_record_count=checkpoint.report.examined,
                errors=[{"error_type": item} for item in checkpoint.report.errors],
                completed_at=datetime.now(UTC),
            )
            self.store.save(checkpoint)

    def _persist_page(self, checkpoint, records, source, theme, lane, key, cursor, turn, finished):
        """Commit the provider page and next cursor atomically in scientific SQLite."""

        candidate = checkpoint.model_copy(deep=True)
        with self.store.database.transaction() as connection:
            for rank, record in enumerate(records, cursor.offset + 1):
                existed = (
                    connection.execute(
                        "SELECT id FROM bibliographic_records WHERE doi=? COLLATE NOCASE "
                        "UNION ALL SELECT id FROM articles WHERE doi=? COLLATE NOCASE LIMIT 1",
                        (record.doi, record.doi),
                    ).fetchone()
                    if record.doi
                    else None
                )
                previously_ready = ready_content(connection, str(existed[0])) if existed else None
                record_id = self.bibliography.upsert_hit(
                    run_id=candidate.harvest_run_id,
                    theme=theme.key,
                    rank=rank,
                    record=record,
                    _connection=connection,
                )
                candidate.report.examined += 1
                if existed:
                    candidate.report.duplicates += 1
                if not record_id:
                    candidate.report.rejected += 1
                    continue
                baseline = previously_ready or ready_content(connection, record_id)
                connection.execute(
                    "INSERT INTO bibliographic_watch_items(record_id, initially_ready) "
                    "VALUES (?, ?) "
                    "ON CONFLICT(record_id) DO UPDATE SET pending=1",
                    (record_id, int(baseline is not None)),
                )
                row = connection.execute(
                    "SELECT relevance_status, doi, manual_decision FROM "
                    "bibliographic_records WHERE id=?",
                    (record_id,),
                ).fetchone()
                status = str(row["relevance_status"])
                if status == "accepted" and not row["doi"] and not row["manual_decision"]:
                    status = "review"
                    connection.execute(
                        "UPDATE bibliographic_records SET relevance_status='review', "
                        "embedding_status='not_applicable' WHERE id=?",
                        (record_id,),
                    )
                if record_id not in candidate.record_ids:
                    candidate.record_ids.append(record_id)
                    setattr(candidate.report, status, getattr(candidate.report, status) + 1)
                if status == "accepted" and not existed:
                    cursor.rotation_gain += 1
            cursor.offset += PAGE_SIZE
            candidate.lane_counts[lane] = candidate.lane_counts.get(lane, 0) + len(records)
            if len(records) < PAGE_SIZE:
                finished.add(key)
                cursor.offset = 0
                cursor.no_gain_rotations = (
                    0 if cursor.rotation_gain else cursor.no_gain_rotations + 1
                )
                cursor.rotation_gain = 0
                if lane == "recent":
                    cursor.since = (
                        date.fromisoformat(cursor.until) - timedelta(days=7)
                    ).isoformat()
                    cursor.until = None
            self.store.put(key, cursor.model_dump(mode="json"), connection=connection)
            self.store.put("turn", turn + 1, connection=connection)
            self.store.save(candidate, connection=connection)
        checkpoint.report = candidate.report
        checkpoint.record_ids = candidate.record_ids
        checkpoint.lane_counts = candidate.lane_counts
        if cursor.no_gain_rotations >= 2:
            self.closures.close_weekly(
                profile=key,
                source=source,
                query_set=theme.key,
                reason="two_complete_rotations_without_gain",
                consecutive_no_gain_runs=cursor.no_gain_rotations,
            )
        elif cursor.rotation_gain:
            self.closures.clear(key)
