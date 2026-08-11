from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.corpus_packages.signatures import (
    PackageSignatureError,
    sign_corpus_package,
    verify_corpus_package_signatures,
)


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="Windows OpenSSH is unavailable")
def test_ed25519_package_signature_detects_artifact_tampering(tmp_path) -> None:
    package = tmp_path / "corpus-v1-test"
    package.mkdir()
    (package / "manifest.json").write_text('{"format_version":1}\n', encoding="utf-8")
    (package / "corpus.zip").write_bytes(b"immutable archive")
    key = tmp_path / "signing"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    public_key = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"ciderscholar-admin {public_key}\n", encoding="utf-8")

    manifest = sign_corpus_package(
        package,
        private_key=key,
        signer_identity="ciderscholar-admin",
    )

    assert manifest.algorithm == "ssh-ed25519"
    assert (
        verify_corpus_package_signatures(
            package,
            allowed_signers=allowed,
        )
        == manifest
    )
    (package / "corpus.zip").write_bytes(b"tampered")
    with pytest.raises(PackageSignatureError, match="mismatch"):
        verify_corpus_package_signatures(package, allowed_signers=allowed)


def test_signature_verification_streams_signed_artifacts(tmp_path, monkeypatch) -> None:
    package = tmp_path / "corpus-v1-test"
    package.mkdir()
    payloads = {
        "manifest.json": b'{"format_version":1}\n',
        "corpus.zip": b"streamed immutable archive" * 100_000,
    }
    artifacts = []
    for filename, payload in payloads.items():
        (package / filename).write_bytes(payload)
        signature_file = f"{filename}.sig"
        (package / signature_file).write_bytes(b"signature")
        artifacts.append(
            {
                "relative_path": filename,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "signature_file": signature_file,
            }
        )
    (package / "signatures.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "algorithm": "ssh-ed25519",
                "namespace": "ciderscholar-corpus-v1",
                "signer_identity": "ciderscholar-admin",
                "public_key_fingerprint": "SHA256:abcdefghijklmnopqrst",
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )
    allowed = tmp_path / "allowed_signers"
    allowed.write_text("allowed", encoding="utf-8")
    streamed_payloads: list[bytes] = []

    def successful_verification(*_args, stdin, **_kwargs):
        streamed_payloads.append(stdin.read())
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr("app.corpus_packages.signatures._ssh_keygen", lambda: "ssh-keygen")
    monkeypatch.setattr("app.corpus_packages.signatures.subprocess.run", successful_verification)
    original_read_bytes = Path.read_bytes

    def reject_zip_bulk_read(path: Path) -> bytes:
        if path.name == "corpus.zip":
            raise AssertionError("signed corpus archive must be streamed to OpenSSH")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_zip_bulk_read)

    verify_corpus_package_signatures(package, allowed_signers=allowed)

    assert streamed_payloads == [payloads["manifest.json"], payloads["corpus.zip"]]
