import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.desktop.app_updates import ApplicationUpdateState, check_application_update
from app.jobs.repository import JobRepository


def _published_installer(settings, tmp_path: Path, *, version: str = "0.3.0") -> Path:
    root = tmp_path / "CiderScholar"
    installers = root / "installers"
    installers.mkdir(parents=True)
    installer = installers / f"CiderScholar-{version}-windows-x64.exe"
    installer.write_bytes(b"verified installer")
    (installers / "latest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "filename": installer.name,
                "size_bytes": installer.stat().st_size,
                "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
                "minimum_windows_build": 22000,
                "published_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    settings.distribution.enabled = True
    settings.distribution.synchronized_root = root
    return installer


def test_application_update_is_distinct_verified_and_available(settings, tmp_path: Path) -> None:
    installer = _published_installer(settings, tmp_path)

    status = check_application_update(settings)

    assert status.state is ApplicationUpdateState.AVAILABLE
    assert status.installer_path == str(installer)
    assert status.available_version == "0.3.0"

    installer.write_bytes(b"altered")
    assert check_application_update(settings).state is ApplicationUpdateState.INVALID


def test_application_update_is_deferred_while_any_durable_job_is_active(
    settings, tmp_path: Path
) -> None:
    _published_installer(settings, tmp_path)
    repository = JobRepository(settings.paths.database_path)
    repository.initialize()
    repository.enqueue_weekly_maintenance()

    status = check_application_update(settings)

    assert status.state is ApplicationUpdateState.DEFERRED_ACTIVE_JOBS
    assert status.active_jobs == 1
    assert status.installer_path is None


def test_current_or_older_release_never_requests_replacement(settings, tmp_path: Path) -> None:
    _published_installer(settings, tmp_path, version="0.2.0")
    assert check_application_update(settings).state is ApplicationUpdateState.CURRENT
