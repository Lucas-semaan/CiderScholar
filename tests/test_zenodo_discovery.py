from __future__ import annotations

from scripts import harvest_zenodo_discovery


def test_zenodo_discovery_delegates_to_shared_staging_workflow(monkeypatch) -> None:
    received: list[str] = []

    def fake_harvest(arguments: list[str]) -> int:
        received.extend(arguments)
        return 7

    monkeypatch.setattr(harvest_zenodo_discovery, "harvest_official_discovery", fake_harvest)

    assert harvest_zenodo_discovery.main(["--run-dir", "staging"]) == 7
    assert received == ["--provider", "zenodo", "--run-dir", "staging"]
