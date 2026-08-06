"""Create the optional corpus/chat backup offered before complete uninstall."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import load_settings
from app.desktop.uninstall_backup import create_uninstall_backup


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    create_uninstall_backup(load_settings(arguments.config), arguments.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
