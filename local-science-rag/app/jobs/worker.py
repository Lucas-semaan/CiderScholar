"""Framework-independent durable worker contracts and orchestration."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from time import monotonic
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.jobs.contracts import JobErrorKind, JobState, JobStep, JobType
from app.jobs.repository import JobRecord, JobRepository
from app.llm.argo_client import (
    ArgoAuthenticationError,
    ArgoAuthorizationError,
    ArgoLocalQuotaError,
    ArgoQuotaError,
    ArgoScientificValidationError,
    ArgoUnavailableError,
)
from app.services.chatbot import ChatbotNoSourcesError

_PROCESS_WORKER_ID = f"worker-{uuid4().hex}"


class JobLeaseLostError(RuntimeError):
    """Raised when a handler can no longer mutate its leased job."""


class JobCancelledError(RuntimeError):
    """Internal control flow carrying an honestly cancelled durable job."""

    def __init__(self, job: JobRecord) -> None:
        self.job = job
        super().__init__(f"job {job.id} was cancelled")


class UnknownJobTypeError(LookupError):
    """Raised before execution when no closed handler exists for a job type."""


@dataclass(frozen=True, slots=True)
class JobHandlerResult:
    """Persistable result returned by a successful job handler."""

    assistant_content: str
    assistant_response: dict[str, Any]
    response_time_milliseconds: float


class JobHandler(Protocol):
    """Closed worker-facing interface implemented by each durable job type."""

    def handle(self, job: JobRecord, context: JobProgressContext) -> JobHandlerResult:
        """Execute one leased job and report a persistable result."""


class JobHandlerRegistry:
    """Closed mapping from accepted durable types to their handlers."""

    def __init__(self, handlers: Mapping[JobType, JobHandler]) -> None:
        unknown_keys = set(handlers) - set(JobType)
        if unknown_keys:
            raise UnknownJobTypeError(f"unknown registered job type: {unknown_keys!r}")
        self._handlers = dict(handlers)

    def resolve(self, job_type: JobType | str) -> JobHandler:
        try:
            normalized = JobType(job_type)
        except ValueError as error:
            raise UnknownJobTypeError(f"unknown job type: {job_type!r}") from error
        try:
            return self._handlers[normalized]
        except KeyError as error:
            raise UnknownJobTypeError(f"no handler registered for {normalized.value}") from error

    def close(self) -> None:
        """Close each distinct handler resource that offers a close method."""

        closed_ids: set[int] = set()
        for handler in self._handlers.values():
            if id(handler) in closed_ids:
                continue
            close = getattr(handler, "close", None)
            if callable(close):
                close()
            closed_ids.add(id(handler))


@dataclass(frozen=True, slots=True)
class JobProgressContext:
    """Give handlers bounded progress and lease operations, without HTTP concerns."""

    repository: JobRepository
    job_id: UUID
    worker_id: str
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def publish(self, step: JobStep, *, technical_message: str | None = None) -> JobRecord:
        updated = self.repository.transition_step(
            self.job_id,
            worker_id=self.worker_id,
            step=step,
            technical_message=technical_message,
            now=self.clock(),
        )
        if updated is None:
            raise JobLeaseLostError("job is no longer running under this worker lease")
        return updated

    def heartbeat(self, lease_duration: timedelta) -> JobRecord:
        updated = self.repository.heartbeat(
            self.job_id,
            worker_id=self.worker_id,
            lease_duration=lease_duration,
            now=self.clock(),
        )
        if updated is None:
            raise JobLeaseLostError("job lease cannot be renewed")
        return updated

    def check_cancellation(self) -> None:
        """Honor a persisted request at a safe boundary or continue normally."""

        current = self.repository.get(self.job_id)
        if current is None:
            raise JobLeaseLostError("job disappeared while checking cancellation")
        if current.state is JobState.CANCEL_REQUESTED:
            cancelled = self.repository.acknowledge_cancellation(
                self.job_id,
                worker_id=self.worker_id,
                now=self.clock(),
            )
            if cancelled is None:
                raise JobLeaseLostError("cancellation request could not be acknowledged")
            raise JobCancelledError(cancelled)
        if current.state is not JobState.RUNNING or current.worker_id != self.worker_id:
            raise JobLeaseLostError("job is no longer owned by this handler")


class DurableJobWorker:
    """Run durable jobs synchronously, one claim at a time."""

    def __init__(
        self,
        *,
        repository: JobRepository,
        registry: JobHandlerRegistry,
        worker_id: str | None = None,
        lease_duration: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
        logger: logging.Logger | None = None,
        terminal_notifier: Callable[[JobRecord], bool] | None = None,
        accepted_job_types: frozenset[JobType] | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.worker_id = worker_id or _PROCESS_WORKER_ID
        self.lease_duration = lease_duration
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.logger = logger or logging.getLogger("ciderscholar.jobs.worker")
        self.terminal_notifier = terminal_notifier
        self.accepted_job_types = accepted_job_types

    def run_once(self) -> JobRecord | None:
        """Claim and complete at most one job; return None when the queue is empty."""

        cycle_started_at = self.clock()
        cycle_started_monotonic = self.monotonic_clock()
        self.repository.recover_expired_leases(now=cycle_started_at)
        job = self.repository.claim_next(
            worker_id=self.worker_id,
            lease_duration=self.lease_duration,
            job_types=(
                tuple(sorted(self.accepted_job_types, key=lambda job_type: job_type.value))
                if self.accepted_job_types is not None
                else None
            ),
            now=cycle_started_at,
        )
        if job is None:
            return None
        handler = self.registry.resolve(job.type)
        context = JobProgressContext(
            repository=self.repository,
            job_id=job.id,
            worker_id=self.worker_id,
            clock=self.clock,
        )
        try:
            with self._maintain_lease(job.id) as heartbeat_lost:
                result = handler.handle(job, context)
                if heartbeat_lost.is_set():
                    raise JobLeaseLostError("job lease was lost during handler execution")
        except JobCancelledError as error:
            return self._logged_result(error.job, cycle_started_monotonic)
        except ArgoLocalQuotaError as error:
            deferred = self.repository.defer_for_quota(
                job.id,
                worker_id=self.worker_id,
                retry_at=error.retry_at,
                now=self.clock(),
            )
            if deferred is None:
                raise JobLeaseLostError("ARGO quota deferral could not be persisted") from None
            return self._logged_result(deferred, cycle_started_monotonic)
        except ArgoQuotaError:
            deferred_at = self.clock()
            deferred = self.repository.defer_for_quota(
                job.id,
                worker_id=self.worker_id,
                retry_at=deferred_at + timedelta(minutes=1),
                now=deferred_at,
            )
            if deferred is None:
                raise JobLeaseLostError(
                    "remote ARGO quota deferral could not be persisted"
                ) from None
            return self._logged_result(deferred, cycle_started_monotonic)
        except ArgoUnavailableError:
            failed = self.repository.fail_attempt(
                job.id,
                worker_id=self.worker_id,
                error_code=JobErrorKind.TIMEOUT,
                safe_message="ARGO n'a pas répondu dans le délai imparti.",
                now=self.clock(),
            )
            if failed is None:
                raise JobLeaseLostError("ARGO timeout could not be persisted") from None
            return self._logged_result(failed, cycle_started_monotonic)
        except ArgoAuthenticationError:
            failed = self.repository.fail_attempt(
                job.id,
                worker_id=self.worker_id,
                error_code=JobErrorKind.AUTHENTICATION,
                safe_message=("La clé ARGO personnelle doit être remplacée dans les paramètres."),
                now=self.clock(),
            )
            if failed is None:
                raise JobLeaseLostError(
                    "ARGO authentication failure could not be persisted"
                ) from None
            return self._logged_result(failed, cycle_started_monotonic)
        except ArgoAuthorizationError:
            failed = self.repository.fail_attempt(
                job.id,
                worker_id=self.worker_id,
                error_code=JobErrorKind.VALIDATION,
                safe_message=(
                    "ARGO refuse cette opération ou ce modèle pour ce compte. "
                    "Testez la connexion dans les paramètres ou contactez le support ARGO."
                ),
                now=self.clock(),
            )
            if failed is None:
                raise JobLeaseLostError(
                    "ARGO authorization failure could not be persisted"
                ) from None
            return self._logged_result(failed, cycle_started_monotonic)
        except ArgoScientificValidationError:
            failed = self.repository.fail_attempt(
                job.id,
                worker_id=self.worker_id,
                error_code=JobErrorKind.TIMEOUT,
                safe_message=(
                    "ARGO a produit une réponse non validable ; "
                    "une nouvelle génération scientifique complète est planifiée."
                ),
                now=self.clock(),
            )
            if failed is None:
                raise JobLeaseLostError(
                    "scientific answer validation failure could not be persisted"
                ) from None
            return self._logged_result(failed, cycle_started_monotonic)
        except ChatbotNoSourcesError:
            failed = self.repository.fail_attempt(
                job.id,
                worker_id=self.worker_id,
                error_code=JobErrorKind.VALIDATION,
                safe_message=(
                    "Aucune source scientifique qualifiée n'est disponible pour répondre "
                    "à cette question."
                ),
                now=self.clock(),
            )
            if failed is None:
                raise JobLeaseLostError(
                    "source-unavailable failure could not be persisted"
                ) from None
            return self._logged_result(failed, cycle_started_monotonic)
        completed = self.repository.persist_result_and_succeed(
            job.id,
            worker_id=self.worker_id,
            assistant_content=result.assistant_content,
            assistant_response=result.assistant_response,
            response_time_milliseconds=result.response_time_milliseconds,
            now=self.clock(),
        )
        if completed is None:
            raise JobLeaseLostError("job result could not be persisted under its worker lease")
        return self._logged_result(completed, cycle_started_monotonic)

    @contextmanager
    def _maintain_lease(self, job_id: UUID) -> Iterator[Event]:
        """Renew one lease while a potentially long handler is running."""

        stop_event = Event()
        lost_event = Event()
        interval = max(0.1, min(30.0, self.lease_duration.total_seconds() / 3))

        def renew() -> None:
            while not stop_event.wait(interval):
                try:
                    heartbeat = self.repository.heartbeat(
                        job_id,
                        worker_id=self.worker_id,
                        lease_duration=self.lease_duration,
                        now=self.clock(),
                    )
                except (OSError, sqlite3.Error):
                    self.logger.warning("job_heartbeat_failed", extra={"job_id": str(job_id)})
                    continue
                if heartbeat is None:
                    lost_event.set()
                    return

        thread = Thread(
            target=renew,
            name=f"job-heartbeat-{job_id}",
            daemon=True,
        )
        thread.start()
        try:
            yield lost_event
        finally:
            stop_event.set()
            thread.join(timeout=1)

    def _logged_result(self, job: JobRecord, started_at: float) -> JobRecord:
        self.logger.info(
            "job_finished",
            extra={
                "job_id": str(job.id),
                "job_step": job.step.value,
                "duration_milliseconds": max(0.0, (self.monotonic_clock() - started_at) * 1000),
            },
        )
        if self.terminal_notifier is not None and job.state in {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLED,
        }:
            try:
                self.terminal_notifier(job)
            except OSError:
                self.logger.warning(
                    "job_notification_failed",
                    extra={"job_id": str(job.id), "job_step": job.step.value},
                )
        return job

    def run_forever(self, stop_event: Event, *, idle_seconds: float = 0.5) -> int:
        """Process jobs until signalled, waiting interruptibly when the queue is empty."""

        if not 0 < idle_seconds <= 60:
            raise ValueError("idle_seconds must be between zero and 60")
        completed_count = 0
        try:
            while not stop_event.is_set():
                completed = self.run_once()
                if completed is None:
                    stop_event.wait(idle_seconds)
                else:
                    completed_count += 1
        finally:
            self.close()
        return completed_count

    def close(self) -> None:
        """Close resources owned by registered handlers."""

        self.registry.close()
