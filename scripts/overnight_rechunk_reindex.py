"""Run the complete tokenizer-aware rechunk and vector rebuild unattended."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime

from app.config import load_settings
from app.corpora import CorpusScope, settings_for_corpus


def _run(module: str, *arguments: str) -> None:
    command = [sys.executable, "-u", "-m", module, *arguments]
    print(f"step={module} state=starting", flush=True)
    subprocess.run(command, check=True)
    print(f"step={module} state=completed", flush=True)


def main() -> int:
    started = datetime.now(UTC)
    settings = settings_for_corpus(load_settings(), CorpusScope.COMMON)
    report_path = settings.paths.exports_dir / (
        f"overnight-rechunk-reindex-{started:%Y%m%dT%H%M%SZ}.json"
    )
    report: dict[str, object] = {
        "started_at": started.isoformat(),
        "database": str(settings.paths.database_path),
        "status": "running",
    }
    try:
        _run("scripts.rechunk_corpus", "--apply")
        _run("scripts.rebuild_index", "--recreate", "--retry-failed")
        _run("scripts.rebuild_index", "--verify-generation")
        with closing(sqlite3.connect(settings.paths.database_path)) as connection:
            chunk_count, maximum = connection.execute(
                "SELECT COUNT(*), MAX(token_count) FROM chunks"
            ).fetchone()
            fts_count = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
            statuses = dict(
                connection.execute(
                    "SELECT embedding_status, COUNT(*) FROM chunks GROUP BY embedding_status"
                ).fetchall()
            )
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        report.update(
            {
                "status": "completed",
                "completed_at": datetime.now(UTC).isoformat(),
                "chunk_count": int(chunk_count),
                "fts_count": int(fts_count),
                "max_token_count": int(maximum),
                "embedding_statuses": statuses,
                "quick_check": quick_check,
            }
        )
        if (
            quick_check != "ok"
            or chunk_count != fts_count
            or int(maximum) > settings.embeddings.max_sequence_length
            or statuses != {"indexed": chunk_count}
        ):
            raise RuntimeError("final overnight verification failed")
        return 0
    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:1000],
            }
        )
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"overnight_report={report_path}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
