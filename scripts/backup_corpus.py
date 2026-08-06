"""Create a verified backup of the shared scientific corpus."""

from __future__ import annotations

from app.config import load_settings
from app.services.corpus_backup import create_corpus_backup


def main() -> int:
    print(create_corpus_backup(load_settings()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
