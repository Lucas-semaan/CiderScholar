import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from app.database.sqlite import Database
from app.desktop.model_integrity import write_model_manifest
from app.desktop.release_publisher import publish_application_release
from app.desktop.uninstall_backup import create_uninstall_backup
from scripts.build_windows_release import (
    _copy_bundled_common_corpus,
    _copy_verified_models,
    _prune_runtime,
)


def test_release_matrix_and_inno_contract_require_no_runtime_toolchain() -> None:
    root = Path(__file__).resolve().parents[1]
    versions = json.loads((root / "installer" / "versions.json").read_text(encoding="utf-8"))
    script = (root / "installer" / "CiderScholar.iss").read_text(encoding="utf-8")
    requirements = (root / "requirements-runtime.txt").read_text(encoding="utf-8")
    builder = (root / "scripts" / "build_windows_release.py").read_text(encoding="utf-8")

    assert versions["python"]["version"] == "3.12.10"
    assert len(versions["python"]["sha256"]) == 64
    assert "PrivilegesRequired=lowest" in script
    assert "pythonw.exe" in script
    assert "onlyifdoesntexist" in script
    assert "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1" in builder
    assert "model_storage_name(model_name)" in builder
    assert "--reranker-model-dir" in builder
    assert r"\models\*" in script
    assert r"\common-corpus\*" in script
    assert "ShouldInstallBundledCommonCorpus" in script
    assert "-B -m scripts.verify_desktop_install" in script
    for replaceable_directory in ("app", "frontend", "runtime", "scripts"):
        assert f'Type: filesandordirs; Name: "{{app}}\\{replaceable_directory}"' in script
    assert 'Type: files; Name: "{app}\\LICENSE"' in script
    assert 'Type: files; Name: "{app}\\requirements-runtime.txt"' in script
    assert r"\common\database\science_rag.sqlite3')) and" in script
    assert r"\common\qdrant\collection'));" in script
    assert (
        "DelTree(ExpandConstant("
        "'{localappdata}\\CiderScholar\\UserData\\data\\common'" not in script
    )
    assert "_copy_bundled_common_corpus" in builder
    assert "comparetimestamp" in script
    assert "SuppressibleMsgBox" in script
    assert "MB_DEFBUTTON2, IDNO" in script
    assert "node" not in requirements.casefold()
    assert '"-B", "-c", code' in builder


def test_runtime_pruning_removes_development_payload_and_archives_licenses(
    tmp_path: Path,
) -> None:
    site_packages = tmp_path / "runtime" / "Lib" / "site-packages"
    include = site_packages / "torch" / "include"
    include.mkdir(parents=True)
    (include / "module.h").write_text("header", encoding="utf-8")
    licenses = site_packages / "torch-2.13.dist-info" / "licenses" / "third_party"
    licenses.mkdir(parents=True)
    (licenses / "LICENSE").write_text("license", encoding="utf-8")

    _prune_runtime(site_packages)

    assert not include.exists()
    archive = tmp_path / "runtime" / "THIRD_PARTY_LICENSES.zip"
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("torch-2.13.dist-info/licenses/third_party/LICENSE") == b"license"


def test_release_staging_copies_both_fingerprinted_models(tmp_path: Path) -> None:
    embedding = tmp_path / "embedding"
    reranker = tmp_path / "reranker"
    for root, model_name in (
        (embedding, "intfloat/multilingual-e5-base"),
        (reranker, "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
    ):
        root.mkdir()
        (root / "model.safetensors").write_bytes(model_name.encode())
        write_model_manifest(root, model_name)
    staging = tmp_path / "staging"
    staging.mkdir()

    _copy_verified_models(staging, embedding, reranker)

    names = {path.name for path in (staging / "models").iterdir()}
    assert names == {
        "intfloat--multilingual-e5-base",
        "cross-encoder--mmarco-mMiniLMv2-L12-H384-v1",
    }


def test_release_staging_copies_retrieval_stores_without_pdf_payload(tmp_path: Path) -> None:
    source = tmp_path / "common"
    database = source / "database"
    qdrant = source / "qdrant" / "collection"
    pdf = source / "pdf"
    database.mkdir(parents=True)
    qdrant.mkdir(parents=True)
    pdf.mkdir()
    (database / "science_rag.sqlite3").write_bytes(b"database")
    (qdrant / "storage.sqlite").write_bytes(b"vectors")
    (source / "qdrant" / ".runtime.lock").write_text("locked", encoding="utf-8")
    (pdf / "source.pdf").write_bytes(b"pdf")
    staging = tmp_path / "staging"
    staging.mkdir()

    _copy_bundled_common_corpus(staging, source)

    bundled = staging / "common-corpus"
    assert (bundled / "database" / "science_rag.sqlite3").read_bytes() == b"database"
    assert (bundled / "qdrant" / "collection" / "storage.sqlite").read_bytes() == b"vectors"
    assert not (bundled / "qdrant" / ".runtime.lock").exists()
    assert not (bundled / "pdf").exists()
    assert (bundled / ".ciderscholar-bundled-corpus").read_text(encoding="ascii") == "default-rag\n"


def test_uninstall_backup_contains_corpus_and_durable_data_but_no_secret(
    settings, tmp_path: Path
) -> None:
    Database(settings.paths.database_path).initialize()
    settings.paths.common_dir.mkdir(parents=True, exist_ok=True)
    (settings.paths.common_dir / "common.txt").write_text("common", encoding="utf-8")
    secret = settings.paths.data_dir / "secrets" / "argo-key.dpapi"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"ciphertext")

    archive = create_uninstall_backup(settings, tmp_path / "backup.zip")

    with zipfile.ZipFile(archive) as backup:
        names = set(backup.namelist())
        assert names == {
            "manifest.json",
            "conversations-and-jobs.sqlite3",
            "corpus.zip",
        }
        assert all("secret" not in name for name in names)


def test_hashed_application_release_publishes_latest_last(tmp_path: Path, monkeypatch) -> None:
    release = tmp_path / "release"
    release.mkdir()
    installer = release / "CiderScholar-0.2.0-windows-x64.exe"
    installer.write_bytes(b"installer")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    installer.with_suffix(".exe.sha256").write_text(
        f"{digest}  {installer.name}\n", encoding="ascii"
    )
    manifest = {
        "schema_version": 1,
        "version": "0.2.0",
        "filename": installer.name,
        "size_bytes": installer.stat().st_size,
        "sha256": digest,
        "minimum_windows_build": 22000,
        "published_at": datetime.now(UTC).isoformat(),
    }
    (release / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    synchronized = tmp_path / "CiderScholar"
    synchronized.mkdir()

    latest = publish_application_release(release, synchronized)
    original_read_bytes = Path.read_bytes

    def reject_installer_bulk_read(path: Path) -> bytes:
        if path.suffix.casefold() == ".exe":
            raise AssertionError("published installers must be compared as streams")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_installer_bulk_read)
    republished = publish_application_release(release, synchronized)
    monkeypatch.undo()

    assert republished == latest
    assert json.loads(latest.read_text(encoding="utf-8"))["sha256"] == digest
    assert (latest.parent / installer.name).read_bytes() == b"installer"
    assert not any(path.name.startswith(".publish-") for path in latest.parent.iterdir())
