"""Copy existing historical chunk vectors into the common Qdrant index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.services.legacy_vector_merge import transfer_legacy_vectors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    arguments = parser.parse_args(argv)
    report = transfer_legacy_vectors(load_settings(arguments.config))
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
