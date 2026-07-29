"""Run Uvicorn with a cooperative stop marker for the desktop supervisor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from threading import Event, Thread

import uvicorn

from app.config import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    return parser


def _watch_stop_file(server: uvicorn.Server, stop_file: Path, stopped: Event) -> None:
    while not stopped.wait(0.25):
        if stop_file.is_file():
            server.should_exit = True
            return


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    os.environ["CIDERSCHOLAR_CONFIG_PATH"] = str(arguments.config.resolve())
    settings = load_settings(arguments.config)
    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host=settings.app.host,
            port=settings.app.api_port,
            log_level=settings.app.log_level.casefold(),
            access_log=False,
        )
    )
    stopped = Event()
    watcher = Thread(
        target=_watch_stop_file,
        args=(server, arguments.stop_file.resolve(), stopped),
        daemon=True,
    )
    watcher.start()
    try:
        server.run()
    finally:
        stopped.set()
        watcher.join(timeout=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
