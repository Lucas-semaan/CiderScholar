"""Publish a locally built, hashed Windows release to a synchronized SharePoint root."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.desktop.release_publisher import publish_application_release


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_directory", type=Path)
    parser.add_argument("synchronized_root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    published = publish_application_release(
        arguments.release_directory,
        arguments.synchronized_root,
    )
    print(published)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
