from __future__ import annotations

import shutil
import subprocess

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
