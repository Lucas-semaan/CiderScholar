"""Restore a verified backup of the shared scientific corpus."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_settings
from app.services.corpus_backup import restore_corpus_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    arguments = parser.parse_args()
    previous = restore_corpus_backup(load_settings(), arguments.archive)
    print(f"restored; previous={previous or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
