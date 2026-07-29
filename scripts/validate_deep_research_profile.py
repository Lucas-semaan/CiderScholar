"""Run the fixed deep-research checks on one physical 8 GB or 16 GB Windows host."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

from app.config import load_settings
from app.deep_research.cache import combined_corpus_fingerprint
from app.evaluation.deep_research_profiles import (
    ProfileCheckResult,
    build_profile_trial_report,
    write_profile_report,
)

CHECKS = {
    "resume": (
        "tests/test_deep_research_job.py"
        "::test_deep_research_resumes_checkpoint_without_resubmitting_question"
    ),
    "cancellation": (
        "tests/test_deep_research_job.py"
        "::test_deep_research_cancellation_stops_at_the_next_checkpoint"
    ),
    "cache_private": (
        "tests/test_deep_research_job.py"
        "::test_production_worker_runs_scoped_search_through_durable_stages"
    ),
    "no_leak": "tests/test_job_worker.py::test_worker_logs_only_structured_ids_steps_and_durations",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("8gb", "16gb"), required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _source_revision() -> str:
    """Hash the effective source tree, including relevant uncommitted files."""

    roots = (Path("app"), Path("scripts"), Path("tests"), Path("frontend/src"))
    standalone = (
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path("frontend/package.json"),
        Path("frontend/package-lock.json"),
    )
    files = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    files.extend(path for path in standalone if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.as_posix()):
        digest.update(path.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_hash(node_id: str) -> str:
    source = Path(node_id.split("::", 1)[0])
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _process_rss_gb(process: psutil.Process) -> float:
    try:
        processes = [process, *process.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0
    total = 0
    for item in processes:
        try:
            total += item.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total / 1024**3


def _run_check(name: str, node_id: str) -> tuple[ProfileCheckResult, float, float]:
    started = time.monotonic()
    peak_process = 0.0
    peak_system = 0.0
    with tempfile.TemporaryFile() as captured:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", node_id, "-q"],
            stdout=captured,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONHASHSEED": "0"},
        )
        observed = psutil.Process(process.pid)
        while process.poll() is None:
            peak_process = max(peak_process, _process_rss_gb(observed))
            peak_system = max(peak_system, psutil.virtual_memory().used / 1024**3)
            time.sleep(0.05)
        peak_system = max(peak_system, psutil.virtual_memory().used / 1024**3)
        captured.seek(0)
        output_sha256 = hashlib.sha256(captured.read()).hexdigest()
    result = ProfileCheckResult(
        name=name,
        test_node_id=node_id,
        source_sha256=_source_hash(node_id),
        passed=process.returncode == 0,
        duration_seconds=time.monotonic() - started,
        output_sha256=output_sha256,
    )
    return result, peak_process, peak_system


def main() -> None:
    args = parse_args()
    settings = load_settings(args.config)
    checks: list[ProfileCheckResult] = []
    peak_process = 0.0
    peak_system = 0.0
    for name, node_id in CHECKS.items():
        result, process_peak, system_peak = _run_check(name, node_id)
        checks.append(result)
        peak_process = max(peak_process, process_peak)
        peak_system = max(peak_system, system_peak)
        print(f"{name}={'passed' if result.passed else 'failed'}")
    host_material = "|".join(
        (
            platform.node(),
            platform.platform(),
            str(psutil.virtual_memory().total),
        )
    )
    report = build_profile_trial_report(
        profile=args.profile,
        detected_total_memory_gb=psutil.virtual_memory().total / 1024**3,
        platform=platform.platform(),
        python_version=platform.python_version(),
        host_fingerprint_sha256=hashlib.sha256(host_material.encode()).hexdigest(),
        code_revision=_source_revision(),
        corpus_sha256=combined_corpus_fingerprint(settings),
        peak_test_process_rss_gb=peak_process,
        peak_system_used_gb=peak_system,
        checks=checks,
    )
    written = write_profile_report(report, args.output)
    print(f"report={written}")
    print(f"sha256={report.report_sha256}")
    print(f"passed={str(report.passed).lower()}")
    for failure in report.failures:
        print(f"failure={failure}")
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
