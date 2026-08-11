"""Run the durable local chat worker once or continuously."""

from __future__ import annotations

import argparse
import json
import logging
import signal
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from app.admin.maintenance_handler import WeeklyMaintenanceHandler
from app.admin.secrets import AdminBibliographicKeyVault
from app.config import Settings, load_settings
from app.corpora import CorpusScope, LocalProfile, load_local_profile, settings_for_corpus
from app.database.sqlite import Database
from app.deep_research.pipeline import build_deep_research_operations
from app.desktop.notifications import WindowsJobNotifier
from app.diagnostics import worker_heartbeat
from app.jobs.background_handlers import CorpusIngestionHandler, LongSynthesisHandler
from app.jobs.chat_handler import ChatAnswerHandler
from app.jobs.contracts import JobType
from app.jobs.deep_research_handler import DeepResearchHandler, DeepResearchOperations
from app.jobs.repository import JobRepository
from app.jobs.worker import DurableJobWorker, JobHandler, JobHandlerRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Path to config.yaml")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one available job, then exit",
    )
    parser.add_argument(
        "--stop-file",
        type=Path,
        help="Cooperative desktop shutdown marker checked without interrupting an active job",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        default=0.5,
        help="Interruptible wait while the queue is empty (continuous mode)",
    )
    parser.add_argument(
        "--chat-concurrency",
        type=int,
        help="Concurrent complete chat workflows (default: configured ARGO minute capacity)",
    )
    return parser


def build_worker(
    settings: Settings,
    *,
    deep_research_operations: DeepResearchOperations | None = None,
    job_types: frozenset[JobType] | None = None,
    worker_id: str | None = None,
    lease_recovery_enabled: bool = True,
) -> DurableJobWorker:
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    requested_types = job_types or frozenset(JobType)
    handlers: dict[JobType, JobHandler] = {}
    if JobType.CHAT_ANSWER in requested_types:
        handlers[JobType.CHAT_ANSWER] = ChatAnswerHandler(settings, repository.database)
    if JobType.DEEP_RESEARCH in requested_types:
        deep_operations = deep_research_operations or build_deep_research_operations(settings)
        handlers[JobType.DEEP_RESEARCH] = DeepResearchHandler(
            settings.paths.cache_dir / "deep_research_jobs",
            deep_operations,
        )
    if JobType.LONG_SYNTHESIS in requested_types:
        scientific_database = Database(settings.paths.common_database_path)
        scientific_database.initialize()
        handlers[JobType.LONG_SYNTHESIS] = LongSynthesisHandler(
            settings,
            scientific_database,
        )
    if JobType.CORPUS_INGESTION in requested_types:
        corpus_settings = settings_for_corpus(settings, CorpusScope.COMMON)
        corpus_database = Database(corpus_settings.paths.database_path)
        corpus_database.initialize()
        handlers[JobType.CORPUS_INGESTION] = CorpusIngestionHandler(
            corpus_settings,
            corpus_database,
        )
    if JobType.WEEKLY_MAINTENANCE in requested_types and load_local_profile() is LocalProfile.ADMIN:
        handlers[JobType.WEEKLY_MAINTENANCE] = WeeklyMaintenanceHandler(settings)
    return DurableJobWorker(
        repository=repository,
        registry=JobHandlerRegistry(handlers),
        worker_id=worker_id,
        terminal_notifier=WindowsJobNotifier(settings).notify,
        accepted_job_types=frozenset(handlers),
        lease_recovery_enabled=lease_recovery_enabled,
    )


def _install_stop_signals(stop_event: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def _watch_stop_file(stop_event: Event, stop_file: Path) -> None:
    while not stop_event.wait(0.25):
        if stop_file.is_file():
            stop_event.set()
            return


def _run_worker_pool(
    workers: list[DurableJobWorker],
    stop_event: Event,
    *,
    idle_seconds: float,
) -> int:
    """Run independent durable workers and stop all if one exits exceptionally."""

    with ThreadPoolExecutor(
        max_workers=len(workers),
        thread_name_prefix="ciderscholar-job",
    ) as executor:
        futures = [
            executor.submit(worker.run_forever, stop_event, idle_seconds=idle_seconds)
            for worker in workers
        ]
        done, pending = wait(futures, return_when=FIRST_EXCEPTION)
        failure = next(
            (future.exception() for future in done if future.exception() is not None),
            None,
        )
        if failure is not None:
            stop_event.set()
            wait(pending)
            raise failure
        return sum(future.result() for future in futures)


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = load_settings(arguments.config)
    settings.paths.create()
    AdminBibliographicKeyVault(settings, load_local_profile()).hydrate_process_environment()
    logging.basicConfig(
        level=getattr(logging, settings.app.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if arguments.once:
        worker = build_worker(settings)
        try:
            processed = worker.run_once() is not None
        finally:
            worker.close()
        print(json.dumps({"mode": "once", "processed": processed}))
        return 0

    stop_event = Event()
    _install_stop_signals(stop_event)
    watcher = None
    if arguments.stop_file is not None:
        watcher = Thread(
            target=_watch_stop_file,
            args=(stop_event, arguments.stop_file.resolve()),
            daemon=True,
        )
        watcher.start()
    chat_concurrency = (
        arguments.chat_concurrency
        if arguments.chat_concurrency is not None
        else settings.app.chat_worker_concurrency
    )
    if not 1 <= chat_concurrency <= 20:
        raise ValueError("chat concurrency must be between 1 and 20")
    workers = [
        build_worker(
            settings,
            worker_id=f"worker-{uuid4().hex}",
            lease_recovery_enabled=True,
        ),
        *(
            build_worker(
                settings,
                job_types=frozenset({JobType.CHAT_ANSWER}),
                worker_id=f"worker-{uuid4().hex}",
                lease_recovery_enabled=False,
            )
            for _ in range(chat_concurrency - 1)
        ),
    ]
    with worker_heartbeat(settings):
        processed_count = _run_worker_pool(
            workers,
            stop_event,
            idle_seconds=arguments.idle_seconds,
        )
    if watcher is not None:
        watcher.join(timeout=1)
    print(json.dumps({"mode": "continuous", "processed_count": processed_count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
