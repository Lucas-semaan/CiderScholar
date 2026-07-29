"""Create a verified backup containing only private documents and indexes."""

from __future__ import annotations

from app.config import load_settings
from app.services.private_backup import create_private_backup


def main() -> int:
    print(create_private_backup(load_settings()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
