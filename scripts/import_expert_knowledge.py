"""Preview or import a validated local candidate package; never activate it."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from app.config import load_settings
from app.database.sqlite import Database
from app.knowledge.loader import KnowledgeLoadError, load_package
from app.knowledge.repository import (
    KnowledgeRepository,
    KnowledgeRepositoryError,
    assert_package_compatible,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        package = load_package(args.knowledge_dir, source_root=args.source_root)
        assert_package_compatible(package)
        settings = load_settings(args.config)
        result = {
            "package_sha256": package.package_sha256,
            "item_count": len(package.items),
            "applied": False,
            "activated": False,
        }
        if args.apply:
            database = Database(settings.paths.database_path)
            # Existing databases must already be migrated through the ordinary application
            # migration/backup workflow; this command cannot silently migrate a live corpus.
            stored = KnowledgeRepository(database).import_candidate(package)
            result.update(applied=True, release_id=str(stored.id), state=stored.state)
        print(json.dumps(result, sort_keys=True))
        return 0
    except KnowledgeLoadError as error:
        print(
            json.dumps({"applied": False, "issues": [issue.model_dump() for issue in error.issues]})
        )
        return 1
    except KnowledgeRepositoryError as error:
        print(json.dumps({"applied": False, "code": str(error)}))
        return 1
    except (OSError, ValueError, sqlite3.Error) as error:
        print(
            json.dumps(
                {"applied": False, "code": "import_unavailable", "error_type": type(error).__name__}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
