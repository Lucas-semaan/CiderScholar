"""Native Windows directory chooser used by the localhost first-launch assistant."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def choose_synchronized_directory() -> Path | None:
    if os.name != "nt":
        raise RuntimeError("Le sélecteur de dossier du package est réservé à Windows.")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='Choisir le dossier CiderScholar synchronise par SharePoint';"
        "$dialog.ShowNewFolderButton=$false;"
        "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
        "{[Console]::OutputEncoding=[Text.Encoding]::UTF8;Write-Output $dialog.SelectedPath}"
    )
    completed = subprocess.run(  # noqa: S603
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError("Le sélecteur de dossier Windows n'a pas pu être ouvert.")
    selected = completed.stdout.decode("utf-8", errors="strict").strip()
    return Path(selected).resolve() if selected else None
