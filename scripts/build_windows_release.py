"""Build one verified offline Windows x64 payload and compile its Inno installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.desktop.model_integrity import verify_model_manifest
from app.ingestion.embeddings import model_storage_name

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReleaseBuildError(RuntimeError):
    """The installer payload cannot be proven complete and reproducible."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)  # noqa: S603


def _download(url: str, destination: Path, expected_sha256: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _sha256(destination) == expected_sha256:
        return destination
    temporary = destination.with_suffix(f"{destination.suffix}.part")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as target:
            shutil.copyfileobj(response, target)
        if _sha256(temporary) != expected_sha256:
            raise ReleaseBuildError("downloaded CPython archive hash mismatch")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _reset_directory(path: Path) -> None:
    resolved = path.resolve()
    build_root = (PROJECT_ROOT / "build" / "windows").resolve()
    if resolved == build_root or not resolved.is_relative_to(build_root):
        raise ReleaseBuildError("release staging must stay below build/windows")
    if resolved.exists():
        for attempt in range(6):
            shutil.rmtree(resolved, ignore_errors=True)
            if not resolved.exists():
                break
            time.sleep(0.25 * (attempt + 1))
        else:
            quarantine = build_root / (
                f".{resolved.name}-stale-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
            )
            try:
                resolved.replace(quarantine)
            except OSError as exc:
                raise ReleaseBuildError("previous release staging could not be retired") from exc
    resolved.mkdir(parents=True)


def _versions() -> dict[str, object]:
    return json.loads((PROJECT_ROOT / "installer" / "versions.json").read_text(encoding="utf-8"))


def _prepare_embedded_python(staging: Path, cache: Path, versions: dict[str, object]) -> Path:
    python = versions["python"]
    if not isinstance(python, dict):
        raise ReleaseBuildError("invalid Python release matrix")
    version = str(python["version"])
    archive = _download(
        str(python["url"]),
        cache / f"python-{version}-embed-amd64.zip",
        str(python["sha256"]),
    )
    runtime = staging / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(archive) as source:
        source.extractall(runtime)
    pth = runtime / f"python{version.replace('.', '')[:3]}._pth"
    if not pth.is_file():
        raise ReleaseBuildError("embedded CPython path configuration is missing")
    lines = [
        line for line in pth.read_text(encoding="utf-8").splitlines() if line != "#import site"
    ]
    for entry in ("Lib/site-packages", "..", "import site"):
        if entry not in lines:
            lines.append(entry)
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return runtime


def _prepare_wheelhouse(cache: Path, versions: dict[str, object]) -> Path:
    wheelhouse = cache / "wheelhouse-cp312-win-amd64"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    requirements = PROJECT_ROOT / "requirements-runtime.txt"
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--platform=win_amd64",
            "--python-version=3.12",
            "--implementation=cp",
            "--abi=cp312",
            f"--dest={wheelhouse}",
            f"--requirement={requirements}",
        ]
    )
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--no-deps",
            f"pip=={versions['pip']}",
            f"--dest={wheelhouse}",
        ]
    )
    return wheelhouse


def _install_runtime_packages(runtime: Path, wheelhouse: Path) -> None:
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    pip_wheels = sorted(wheelhouse.glob("pip-*.whl"))
    if not pip_wheels:
        raise ReleaseBuildError("pinned pip wheel is missing")
    with zipfile.ZipFile(pip_wheels[-1]) as wheel:
        wheel.extractall(site_packages)
    embedded = runtime / "python.exe"
    _run(
        [
            str(embedded),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--only-binary=:all:",
            f"--find-links={wheelhouse}",
            f"--target={site_packages}",
            f"--requirement={PROJECT_ROOT / 'requirements-runtime.txt'}",
        ]
    )
    _prune_runtime(site_packages)


def _prune_runtime(site_packages: Path) -> None:
    """Remove build tooling, bytecode caches and packaged test suites from the user runtime."""

    runtime = site_packages.parents[1]
    license_archive = runtime / "THIRD_PARTY_LICENSES.zip"
    license_files = sorted(
        path
        for metadata in site_packages.glob("*.dist-info")
        for path in (metadata / "licenses").rglob("*")
        if path.is_file()
    )
    if license_files:
        with zipfile.ZipFile(license_archive, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in license_files:
                archive.write(path, path.relative_to(site_packages).as_posix())
        for metadata in site_packages.glob("*.dist-info"):
            shutil.rmtree(metadata / "licenses", ignore_errors=True)

    removable_roots = [*site_packages.glob("pip"), *site_packages.glob("pip-*.dist-info")]
    for path in removable_roots:
        shutil.rmtree(path, ignore_errors=True)
    for path in sorted(site_packages.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_dir():
            continue
        if path.name == "__pycache__" or path.name.casefold() in {"test", "tests"}:
            shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(site_packages / "torch" / "include", ignore_errors=True)
    shutil.rmtree(site_packages / "torch" / "share", ignore_errors=True)
    for library in (site_packages / "torch" / "lib").glob("*.lib"):
        library.unlink(missing_ok=True)


def _smoke_test_runtime(runtime: Path, application: Path) -> None:
    """Import the packaged application and every heavyweight runtime dependency."""

    source = json.dumps(str(application.resolve()))
    modules = (
        "apscheduler, fastapi, fitz, httpx, numpy, playwright, psutil, pydantic, "
        "qdrant_client, sentence_transformers, torch, transformers, uvicorn, yaml"
    )
    code = f"import sys; sys.path.insert(0, {source}); import {modules}; import app.main"
    try:
        _run([str(runtime / "python.exe"), "-B", "-c", code])
    except subprocess.CalledProcessError as exc:
        raise ReleaseBuildError("pruned embedded runtime smoke test failed") from exc


def _copy_application(staging: Path) -> None:
    application = staging / "application"
    application.mkdir()
    for directory in ("app", "scripts"):
        shutil.copytree(
            PROJECT_ROOT / directory,
            application / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copytree(PROJECT_ROOT / "frontend" / "dist", application / "frontend" / "dist")
    for filename in ("LICENSE", "requirements-runtime.txt"):
        shutil.copy2(PROJECT_ROOT / filename, application / filename)


def _copy_bundled_common_corpus(staging: Path, source: Path) -> None:
    """Add the local RAG stores required for the default common corpus.

    The SQLite database and Qdrant index are sufficient for retrieval. The original
    PDFs stay outside the application installer because they are not needed to answer
    a question and would add several gigabytes to every installation.
    """

    source_root = source.resolve()
    database = source_root / "database" / "science_rag.sqlite3"
    collection = source_root / "qdrant" / "collection"
    if not database.is_file() or not collection.is_dir():
        raise ReleaseBuildError("the bundled common corpus is incomplete")

    destination = staging / "common-corpus"
    shutil.copytree(source_root / "database", destination / "database")
    shutil.copytree(
        source_root / "qdrant",
        destination / "qdrant",
        ignore=shutil.ignore_patterns("*.lock", "*.tmp", "*-wal", "*-shm", "__pycache__"),
    )
    (destination / ".ciderscholar-bundled-corpus").write_text(
        "default-rag\n",
        encoding="ascii",
    )


def _copy_verified_models(
    staging: Path,
    embedding_model: Path,
    reranker_model: Path,
) -> None:
    models = {
        "intfloat/multilingual-e5-base": embedding_model,
        "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1": reranker_model,
    }
    destination_root = staging / "models"
    destination_root.mkdir()
    for model_name, source in models.items():
        verify_model_manifest(source, model_name)
        destination = destination_root / model_storage_name(model_name)
        shutil.copytree(source.resolve(), destination)
        verify_model_manifest(destination, model_name)


def _payload_manifest(staging: Path, versions: dict[str, object]) -> Path:
    files = {
        path.relative_to(staging).as_posix(): {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(staging.rglob("*"))
        if path.is_file() and path.name != "payload-manifest.json"
    }
    payload = {"schema_version": 1, "versions": versions, "files": files}
    destination = staging / "payload-manifest.json"
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return destination


def _verify_payload_manifest(staging: Path) -> None:
    manifest_path = staging / "payload-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = payload["files"]
        if not isinstance(files, dict):
            raise ValueError("payload files must be a mapping")
        actual_names = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file() and path != manifest_path
        }
        if actual_names != set(files):
            raise ValueError("payload file list mismatch")
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, dict):
                raise ValueError("payload entry is invalid")
            path = staging / Path(name)
            if (
                path.stat().st_size != int(expected["size_bytes"])
                or _sha256(path) != expected["sha256"]
            ):
                raise ValueError(f"payload hash mismatch: {name}")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ReleaseBuildError("compile-only payload verification failed") from exc


def _find_iscc(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
        Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
        Path.home() / "AppData/Local/Programs/Inno Setup 6/ISCC.exe",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise ReleaseBuildError("ISCC.exe 6.5 or newer is required to compile the installer")


@contextmanager
def _short_windows_source(staging: Path):
    if sys.platform != "win32":
        yield str(staging)
        return
    drive = "R:"
    subprocess.run(["subst.exe", drive, "/D"], check=False, capture_output=True)  # noqa: S603
    subprocess.run(["subst.exe", drive, str(staging.resolve())], check=True)  # noqa: S603
    try:
        yield f"{drive}\\"
    finally:
        subprocess.run(["subst.exe", drive, "/D"], check=False)  # noqa: S603


def _compile_installer(staging: Path, output: Path, version: str, iscc: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    with _short_windows_source(staging) as source:
        _run(
            [
                str(iscc),
                f"/DSourceRoot={source}",
                f"/DOutputDir={output}",
                f"/DAppVersion={version}",
                str(PROJECT_ROOT / "installer" / "CiderScholar.iss"),
            ]
        )
    installer = output / f"CiderScholar-{version}-windows-x64.exe"
    if not installer.is_file():
        raise ReleaseBuildError("Inno Setup did not create the expected installer")
    digest = _sha256(installer)
    installer.with_suffix(f"{installer.suffix}.sha256").write_text(
        f"{digest}  {installer.name}\n", encoding="ascii"
    )
    latest = {
        "schema_version": 1,
        "version": version,
        "filename": installer.name,
        "size_bytes": installer.stat().st_size,
        "sha256": digest,
        "minimum_windows_build": 22000,
        "published_at": datetime.now(UTC).isoformat(),
    }
    (output / "latest.json").write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    return installer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reranker-model-dir", type=Path, required=True)
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Reuse an already verified build/windows/staging payload",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    versions = _versions()
    build_root = PROJECT_ROOT / "build" / "windows"
    staging = build_root / "staging"
    cache = build_root / "cache"
    output = PROJECT_ROOT / "installer" / "output"
    if arguments.compile_only:
        if not (staging / "payload-manifest.json").is_file():
            raise ReleaseBuildError("compile-only staging manifest is unavailable")
        _verify_payload_manifest(staging)
    else:
        _reset_directory(staging)
        if not arguments.skip_frontend:
            _run(["npm.cmd", "--prefix", "frontend", "ci"])
            _run(["npm.cmd", "--prefix", "frontend", "run", "build"])
        runtime = _prepare_embedded_python(staging, cache, versions)
        wheelhouse = _prepare_wheelhouse(cache, versions)
        _install_runtime_packages(runtime, wheelhouse)
        _copy_application(staging)
        _copy_bundled_common_corpus(staging, PROJECT_ROOT / "data" / "common")
        _smoke_test_runtime(runtime, staging / "application")
        _copy_verified_models(
            staging,
            arguments.model_dir,
            arguments.reranker_model_dir,
        )
        shutil.copy2(PROJECT_ROOT / "installer" / "config.runtime.yaml", staging)
        _payload_manifest(staging, versions)
    installer = _compile_installer(
        staging,
        output,
        str(versions["application"]),
        _find_iscc(arguments.iscc),
    )
    print(installer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
