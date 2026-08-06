"""Optional content-free Windows notifications for terminal durable jobs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.config import Settings
from app.jobs.contracts import JobState, JobType
from app.jobs.repository import JobRecord

TERMINAL_STATES = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


class WindowsJobNotifier:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.notifications.enabled
        self.script = Path(__file__).resolve().parents[2] / "scripts" / "show_job_notification.ps1"

    def notify(self, job: JobRecord) -> bool:
        if (
            not self.enabled
            or sys.platform != "win32"
            or job.state not in TERMINAL_STATES
            or not self.script.is_file()
        ):
            return False
        label = {
            JobType.CHAT_ANSWER: "Réponse scientifique",
            JobType.DEEP_RESEARCH: "Analyse approfondie",
            JobType.WEEKLY_MAINTENANCE: "Maintenance du corpus",
            JobType.LONG_SYNTHESIS: "Synthèse longue",
            JobType.CORPUS_INGESTION: "Ingestion du corpus",
        }[job.type]
        state = {
            JobState.SUCCEEDED: "terminée",
            JobState.FAILED: "en échec",
            JobState.CANCELLED: "annulée",
        }[job.state]
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
                "-Title",
                "CiderScholar",
                "-Message",
                f"{label} {state}.",
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        return True
