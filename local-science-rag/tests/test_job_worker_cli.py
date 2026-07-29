from __future__ import annotations

import json
from threading import Barrier, Event

import scripts.run_job_worker as worker_cli


class _FakeWorker:
    def __init__(self) -> None:
        self.once_calls = 0
        self.forever_calls = 0
        self.closed = False

    def run_once(self):
        self.once_calls += 1
        return object()

    def run_forever(self, stop_event, *, idle_seconds: float) -> int:
        self.forever_calls += 1
        assert idle_seconds == 0.25
        assert not stop_event.is_set()
        return 3

    def close(self) -> None:
        self.closed = True


def test_worker_pool_runs_complete_chat_workers_concurrently() -> None:
    barrier = Barrier(2)
    stop_event = Event()

    class ConcurrentWorker(_FakeWorker):
        def run_forever(self, stop_event, *, idle_seconds: float) -> int:
            assert idle_seconds == 0.25
            barrier.wait(timeout=5)
            stop_event.set()
            return 1

    workers = [ConcurrentWorker(), ConcurrentWorker()]

    assert (
        worker_cli._run_worker_pool(
            workers,
            stop_event,
            idle_seconds=0.25,
        )
        == 2
    )


def test_worker_command_once_mode(settings, monkeypatch, capsys) -> None:
    fake = _FakeWorker()
    monkeypatch.setattr(worker_cli, "load_settings", lambda _path: settings)
    monkeypatch.setattr(worker_cli, "build_worker", lambda _settings: fake)

    assert worker_cli.main(["--once"]) == 0

    assert fake.once_calls == 1
    assert fake.forever_calls == 0
    assert fake.closed is True
    assert json.loads(capsys.readouterr().out) == {"mode": "once", "processed": True}


def test_worker_command_continuous_mode(settings, monkeypatch, capsys) -> None:
    fakes: list[_FakeWorker] = []

    def build_fake(_settings, **_kwargs):
        fake = _FakeWorker()
        fakes.append(fake)
        return fake

    monkeypatch.setattr(worker_cli, "load_settings", lambda _path: settings)
    monkeypatch.setattr(worker_cli, "build_worker", build_fake)
    monkeypatch.setattr(worker_cli, "_install_stop_signals", lambda _event: None)

    assert (
        worker_cli.main(
            ["--idle-seconds", "0.25", "--chat-concurrency", "2"],
        )
        == 0
    )

    assert len(fakes) == 2
    assert all(fake.once_calls == 0 for fake in fakes)
    assert all(fake.forever_calls == 1 for fake in fakes)
    assert json.loads(capsys.readouterr().out) == {
        "mode": "continuous",
        "processed_count": 6,
    }
