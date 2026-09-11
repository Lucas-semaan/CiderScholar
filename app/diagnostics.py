"""Content-free operational readiness checks for local demonstrations."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread

from app.config import Settings
from app.jobs.repository import JobRepository
from app.llm.argo_client import ArgoClient, ArgoHealth
from app.llm.providers import LlmProviderStore
from app.memory import MemoryGuard

WORKER_HEARTBEAT_MAX_AGE_SECONDS = 5
DISK_READINESS_MINIMUM_BYTES = 2 * 1024**3


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def worker_heartbeat_path(settings: Settings) -> Path:
    return settings.paths.data_dir / "runtime" / "worker-heartbeat.json"


def _write_worker_heartbeat(path: Path, pid: int, worker_ids: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pid": pid,
                "worker_ids": list(worker_ids),
                "updated_at": _timestamp(datetime.now(UTC)),
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


@contextmanager
def worker_heartbeat(
    settings: Settings,
    *,
    worker_ids: tuple[str, ...] = (),
    interval_seconds: float = 1.0,
) -> Iterator[None]:
    """Publish liveness while the continuous durable worker owns its loop."""

    path = worker_heartbeat_path(settings)
    pid = os.getpid()
    stopped = Event()

    def publish() -> None:
        while not stopped.is_set():
            _write_worker_heartbeat(path, pid, worker_ids)
            stopped.wait(interval_seconds)

    thread = Thread(target=publish, name="worker-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=max(1.0, interval_seconds * 2))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("pid") == pid:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass


def interrupted_worker_ids(settings: Settings) -> tuple[str, ...]:
    """Return leases owned by a previous heartbeat process that has exited.

    Legacy heartbeats deliberately return no identifiers.  Without an exact owner list,
    early recovery could duplicate work still running in another local process.
    """

    try:
        payload = json.loads(worker_heartbeat_path(settings).read_text(encoding="utf-8"))
        if payload.get("schema_version") != 2:
            return ()
        pid = payload.get("pid")
        raw_worker_ids = payload.get("worker_ids")
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not isinstance(raw_worker_ids, list)
        ):
            return ()
        worker_ids = tuple(
            dict.fromkeys(
                value
                for value in raw_worker_ids
                if isinstance(value, str) and 1 <= len(value) <= 200
            )
        )
        if not worker_ids or _worker_process_is_running(pid):
            return ()
        return worker_ids
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return ()


def _worker_process_is_running(pid: int) -> bool:
    """Fail closed when process liveness cannot be determined reliably."""

    try:
        import psutil
    except ImportError:
        return True
    try:
        process = psutil.Process(pid)
        return bool(process.is_running() and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError, ValueError):
        return True


def build_readiness_report(
    settings: Settings,
    *,
    argo_probe: Callable[[], ArgoHealth] | None = None,
    now: datetime | None = None,
    disk_free_bytes: int | None = None,
) -> dict[str, object]:
    """Run bounded checks; the active LLM uses only its model list and never generation."""

    measured_at = now or datetime.now(UTC)
    checks = {
        "argo": _argo_check(settings, argo_probe),
        "worker": _worker_check(settings, measured_at),
        "corpus": _corpus_check(settings),
        "disk": _disk_check(settings, disk_free_bytes),
    }
    queue = JobRepository(settings.paths.database_path).queue_metrics(now=measured_at)
    return {
        "schema_version": 1,
        "ready": all(check["state"] == "ready" for check in checks.values()),
        "checked_at": _timestamp(measured_at),
        "checks": checks,
        "queue": {
            "depth": queue.depth,
            "queued": queue.queued,
            "running": queue.running,
            "cancel_requested": queue.cancel_requested,
            "oldest_created_at": (
                _timestamp(queue.oldest_created_at) if queue.oldest_created_at else None
            ),
            "oldest_age_seconds": queue.oldest_age_seconds,
        },
    }


def build_runtime_diagnostics(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return bounded operational metadata without payloads, content, or secrets."""

    measured_at = now or datetime.now(UTC)
    heartbeat_at, worker_pid = _read_worker_heartbeat(settings)
    heartbeat_age_seconds = (
        max(0, int((measured_at - heartbeat_at).total_seconds()))
        if heartbeat_at is not None
        else None
    )
    worker_state = (
        "healthy"
        if heartbeat_age_seconds is not None
        and heartbeat_age_seconds <= WORKER_HEARTBEAT_MAX_AGE_SECONDS
        else "stale"
    )
    warnings: list[dict[str, str]] = []
    if heartbeat_at is None:
        warnings.append(
            {
                "code": "worker_heartbeat_missing",
                "severity": "warning",
                "message": "Le heartbeat du worker durable est indisponible.",
            }
        )
    elif worker_state == "stale":
        warnings.append(
            {
                "code": "worker_heartbeat_stale",
                "severity": "warning",
                "message": "Le heartbeat du worker durable est trop ancien.",
            }
        )
    active_jobs = JobRepository(settings.paths.database_path).list_active_diagnostics()
    if active_jobs and worker_state == "stale":
        warnings.append(
            {
                "code": "active_work_without_healthy_worker",
                "severity": "warning",
                "message": "Des travaux actifs attendent un worker durable sain.",
            }
        )
    snapshot = MemoryGuard(settings.memory).snapshot()
    return {
        "checked_at": _timestamp(measured_at),
        "worker": {
            "state": worker_state,
            "heartbeat_at": _timestamp(heartbeat_at) if heartbeat_at is not None else None,
            "heartbeat_age_seconds": heartbeat_age_seconds,
        },
        "process": {
            "api_rss_bytes": int(snapshot.process_rss_gb * 1024**3) if snapshot else None,
            "worker_rss_bytes": _worker_rss_bytes(worker_pid),
            "system_available_bytes": (
                int(snapshot.system_available_gb * 1024**3) if snapshot else None
            ),
        },
        "active_jobs": [
            {
                "id": job.id,
                "type": job.type,
                "state": job.state,
                "step": job.step,
                "created_at": job.created_at,
                "heartbeat_at": job.heartbeat_at,
            }
            for job in active_jobs
        ],
        "warnings": warnings,
    }


def _check(state: str, message: str, action: str) -> dict[str, str]:
    return {"state": state, "message": message, "action": action}


def _argo_check(
    settings: Settings,
    argo_probe: Callable[[], ArgoHealth] | None,
) -> dict[str, str]:
    profile = LlmProviderStore(settings).active_profile()
    configured = profile.key_configured
    if not configured:
        return _check(
            "blocked",
            f"Clé {profile.label} absente.",
            "Ajouter puis tester la clé dans Paramètres.",
        )
    try:
        if argo_probe is None:
            with ArgoClient(settings) as client:
                health = client.health()
        else:
            health = argo_probe()
    except Exception:
        return _check(
            "blocked",
            "Sonde LLM indisponible.",
            "Vérifier le réseau du fournisseur, puis actualiser.",
        )
    if health.reachable and health.model_available:
        return _check(
            "ready", f"Clé et modèle {profile.label} accessibles.", "Aucune action requise."
        )
    if health.reachable:
        return _check(
            "blocked",
            f"Modèle {profile.label} non autorisé pour cette clé.",
            "Choisir un modèle accessible puis actualiser.",
        )
    return _check(
        "blocked",
        f"{profile.label} ne répond pas à la sonde sans génération.",
        "Vérifier le réseau du fournisseur, puis actualiser.",
    )


def _worker_check(settings: Settings, now: datetime) -> dict[str, str]:
    updated_at = _read_worker_heartbeat_at(settings)
    if updated_at is None:
        return _check(
            "blocked",
            "Worker durable non détecté.",
            "Arrêter puis relancer CiderScholar depuis le menu Démarrer.",
        )
    age = max(0.0, (now - updated_at).total_seconds())
    if age > WORKER_HEARTBEAT_MAX_AGE_SECONDS:
        return _check(
            "blocked",
            "Worker durable détecté mais inactif.",
            "Arrêter puis relancer CiderScholar depuis le menu Démarrer.",
        )
    return _check("ready", "Worker durable actif.", "Aucune action requise.")


def _read_worker_heartbeat_at(settings: Settings) -> datetime | None:
    """Read only the timestamp from the runtime heartbeat, never exposing its PID."""

    return _read_worker_heartbeat(settings)[0]


def _read_worker_heartbeat(settings: Settings) -> tuple[datetime | None, int | None]:
    """Read internal worker liveness metadata while keeping the PID private."""

    try:
        payload = json.loads(worker_heartbeat_path(settings).read_text(encoding="utf-8"))
        timestamp = datetime.fromisoformat(str(payload["updated_at"]))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return None, None
        pid = payload.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            pid = None
        return timestamp.astimezone(UTC), pid
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def _worker_rss_bytes(pid: int | None) -> int | None:
    """Best-effort RSS lookup for the separate worker process, with no PID disclosure."""

    if pid is None:
        return None
    try:
        import psutil
    except ImportError:
        return None
    try:
        return int(psutil.Process(pid).memory_info().rss)
    except (psutil.Error, OSError, ValueError):
        return None


def _corpus_check(settings: Settings) -> dict[str, str]:
    try:
        with closing(sqlite3.connect(settings.paths.common_database_path)) as connection:
            articles = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
            chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    except (OSError, sqlite3.Error, TypeError):
        return _check(
            "blocked",
            "Corpus commun absent ou illisible.",
            "Ouvrir Paramètres et installer à nouveau le corpus commun.",
        )
    if articles == 0 or chunks == 0:
        return _check(
            "blocked",
            "Corpus commun vide.",
            "Installer une version publiée du corpus avant la démonstration.",
        )
    return _check(
        "ready",
        f"Corpus commun prêt : {articles} articles, {chunks} fragments.",
        "Aucune action requise.",
    )


def _disk_check(settings: Settings, free_bytes: int | None) -> dict[str, str]:
    available = free_bytes
    if available is None:
        settings.paths.data_dir.mkdir(parents=True, exist_ok=True)
        available = shutil.disk_usage(settings.paths.data_dir).free
    free_gb = available / 1024**3
    if available < DISK_READINESS_MINIMUM_BYTES:
        return _check(
            "blocked",
            f"Espace disque faible : {free_gb:.1f} Go libres.",
            "Libérer au moins 2 Go avant la démonstration.",
        )
    return _check(
        "ready",
        f"Espace disque disponible : {free_gb:.1f} Go.",
        "Aucune action requise.",
    )
