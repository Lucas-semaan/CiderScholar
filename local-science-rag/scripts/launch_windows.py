"""Launch and supervise the packaged API and durable worker without a console."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from app.config import load_settings
from app.desktop.layout import create_desktop_layout, desktop_paths
from app.desktop.model_integrity import verify_model_manifest
from app.desktop.supervisor import NamedWindowsMutex, request_shutdown, wait_for_health
from app.desktop.system_checks import validate_windows_11_x64
from app.ingestion.embeddings import local_model_path
from app.retrieval.reranker import local_reranker_model_path


def _message(title: str, message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def _child(
    python: Path,
    module: str,
    config: Path,
    stop_file: Path,
    log_path: Path,
    environment: dict[str, str],
) -> tuple[subprocess.Popen[bytes], object]:
    log = log_path.open("ab", buffering=0)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(  # noqa: S603
        [
            str(python),
            "-m",
            module,
            "--config",
            str(config),
            "--stop-file",
            str(stop_file),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    return process, log


def _stop_children(processes: list[subprocess.Popen[bytes]], timeout_seconds: float = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    for process in processes:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            process.wait(timeout=10)


def main() -> int:
    validate_windows_11_x64()
    paths = desktop_paths()
    create_desktop_layout(paths)
    if not paths.config.is_file():
        raise RuntimeError("La configuration de premier lancement est absente.")
    os.environ["CIDERSCHOLAR_CONFIG_PATH"] = str(paths.config)
    settings = load_settings(paths.config)
    verify_model_manifest(local_model_path(settings), settings.embeddings.model_name)
    if settings.reranker.enabled:
        verify_model_manifest(
            local_reranker_model_path(settings),
            settings.reranker.model_name,
        )
    url = f"http://{settings.app.host}:{settings.app.api_port}"
    with NamedWindowsMutex() as mutex:
        if not mutex.acquire():
            wait_for_health(f"{url}/health", timeout_seconds=30)
            webbrowser.open(url)
            return 0
        stop_file = paths.runtime / "shutdown.request"
        stop_file.unlink(missing_ok=True)
        environment = os.environ.copy()
        environment["CIDERSCHOLAR_CONFIG_PATH"] = str(paths.config)
        python = Path(sys.executable).with_name("python.exe")
        children: list[subprocess.Popen[bytes]] = []
        logs: list[object] = []
        try:
            api, api_log = _child(
                python,
                "scripts.run_api",
                paths.config,
                stop_file,
                paths.logs / "api.log",
                environment,
            )
            worker, worker_log = _child(
                python,
                "scripts.run_job_worker",
                paths.config,
                stop_file,
                paths.logs / "worker.log",
                environment,
            )
            children.extend((api, worker))
            logs.extend((api_log, worker_log))
            if not wait_for_health(f"{url}/health"):
                raise RuntimeError("L'API locale n'a pas atteint l'état prêt dans le délai prévu.")
            if worker.poll() is not None:
                raise RuntimeError("Le worker durable s'est arrêté pendant le lancement.")
            webbrowser.open(url)
            while not stop_file.is_file():
                if any(process.poll() is not None for process in children):
                    raise RuntimeError(
                        "Un processus CiderScholar s'est arrêté de manière inattendue."
                    )
                time.sleep(0.25)
        except Exception as exc:
            request_shutdown(stop_file)
            _message("CiderScholar", str(exc))
            return 1
        finally:
            if children:
                request_shutdown(stop_file)
                _stop_children(children)
            for log in logs:
                close = getattr(log, "close", None)
                if callable(close):
                    close()
            stop_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
