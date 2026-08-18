"""Harvest Zenodo into resumable read-only staging without a second SQLite writer."""

from __future__ import annotations

import sys

from scripts.harvest_semantic_scholar_discovery import main as harvest_official_discovery


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return harvest_official_discovery(["--provider", "zenodo", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
