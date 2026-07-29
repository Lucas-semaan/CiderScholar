from __future__ import annotations

import json
from pathlib import Path

from app.diagnostics import build_readiness_report


def test_argo_outage_blocks_generation_and_keeps_fallback_honest(settings, monkeypatch) -> None:
    monkeypatch.setenv(settings.argo.api_key_env, "configured-for-outage-test")
    probe_calls = 0

    def unavailable_probe():
        nonlocal probe_calls
        probe_calls += 1
        raise ConnectionError("provider detail must stay internal")

    report = build_readiness_report(
        settings,
        argo_probe=unavailable_probe,
        disk_free_bytes=3 * 1024**3,
    )

    assert probe_calls == 1
    assert report["ready"] is False
    assert report["checks"]["argo"] == {
        "state": "blocked",
        "message": "Sonde ARGO indisponible.",
        "action": "Vérifier le réseau INRAE ou le VPN, puis actualiser.",
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "provider detail" not in serialized
    assert "answer" not in serialized.casefold()

    runbook = (Path(__file__).parents[1] / "docs" / "DEMO_RUNBOOK.md").read_text(encoding="utf-8")
    normalized_runbook = " ".join(runbook.split())
    assert "ARGO est indisponible ; aucune génération ne sera simulée" in normalized_runbook
    assert "exemple enregistré, non généré pendant cette session" in normalized_runbook
