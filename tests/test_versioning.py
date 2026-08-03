from __future__ import annotations

import json
import tomllib
from pathlib import Path

from app import __version__
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent_across_runtime_and_build_metadata(settings) -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    installer = json.loads(
        (PROJECT_ROOT / "installer" / "versions.json").read_text(encoding="utf-8")
    )
    frontend = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    lockfile = json.loads(
        (PROJECT_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8")
    )

    versions = {
        __version__,
        create_app(settings).version,
        pyproject["project"]["version"],
        installer["application"],
        frontend["version"],
        lockfile["version"],
        lockfile["packages"][""]["version"],
    }
    assert versions == {"0.2.3"}
