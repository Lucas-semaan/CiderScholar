"""Detached OpenSSH Ed25519 signatures for immutable corpus packages."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.corpus_packages.hashing import sha256_file

SIGNATURE_NAMESPACE = "ciderscholar-corpus-v1"
SIGNED_FILES = ("manifest.json", "corpus.zip")


class SignedPackageArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: Literal["manifest.json", "corpus.zip"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_file: str = Field(pattern=r"^(?:manifest\.json|corpus\.zip)\.sig$")


class PackageSignatureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = 1
    algorithm: Literal["ssh-ed25519"] = "ssh-ed25519"
    namespace: Literal["ciderscholar-corpus-v1"] = SIGNATURE_NAMESPACE
    signer_identity: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]{2,127}$")
    public_key_fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{20,100}$")
    artifacts: list[SignedPackageArtifact] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def complete_file_set(self) -> PackageSignatureManifest:
        if {artifact.relative_path for artifact in self.artifacts} != set(SIGNED_FILES):
            raise ValueError("signature manifest must cover manifest.json and corpus.zip")
        return self


class PackageSignatureError(RuntimeError):
    pass


def _ssh_keygen() -> str:
    executable = shutil.which("ssh-keygen")
    if executable is None:
        raise PackageSignatureError("Windows OpenSSH ssh-keygen is unavailable")
    return executable


def _fingerprint(public_key: Path) -> str:
    result = subprocess.run(
        [_ssh_keygen(), "-lf", str(public_key), "-E", "sha256"],
        check=True,
        capture_output=True,
        text=True,
    )
    fields = result.stdout.split()
    if len(fields) < 2 or not fields[1].startswith("SHA256:"):
        raise PackageSignatureError("OpenSSH returned an invalid public-key fingerprint")
    return fields[1]


def sign_corpus_package(
    version_directory: str | Path,
    *,
    private_key: str | Path,
    signer_identity: str,
) -> PackageSignatureManifest:
    root = Path(version_directory).resolve()
    key = Path(private_key).resolve()
    public_key = key.with_suffix(key.suffix + ".pub")
    if not key.is_file() or not public_key.is_file():
        raise PackageSignatureError("private and public OpenSSH signing keys are required")
    artifacts: list[SignedPackageArtifact] = []
    for filename in SIGNED_FILES:
        source = root / filename
        signature = root / f"{filename}.sig"
        if not source.is_file():
            raise PackageSignatureError(f"package artifact is unavailable: {filename}")
        if signature.exists():
            raise PackageSignatureError(f"detached signature already exists: {signature.name}")
        subprocess.run(
            [
                _ssh_keygen(),
                "-Y",
                "sign",
                "-f",
                str(key),
                "-n",
                SIGNATURE_NAMESPACE,
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        if not signature.is_file():
            raise PackageSignatureError(f"OpenSSH did not create {signature.name}")
        artifacts.append(
            SignedPackageArtifact(
                relative_path=filename,
                sha256=sha256_file(source),
                signature_file=signature.name,
            )
        )
    manifest = PackageSignatureManifest(
        signer_identity=signer_identity,
        public_key_fingerprint=_fingerprint(public_key),
        artifacts=artifacts,
    )
    destination = root / "signatures.json"
    temporary = root / ".signatures.json.tmp"
    temporary.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return manifest


def verify_corpus_package_signatures(
    version_directory: str | Path,
    *,
    allowed_signers: str | Path,
) -> PackageSignatureManifest:
    root = Path(version_directory).resolve()
    allowed = Path(allowed_signers).resolve()
    signature_manifest = root / "signatures.json"
    if not allowed.is_file() or not signature_manifest.is_file():
        raise PackageSignatureError("allowed signers or signature manifest is unavailable")
    manifest = PackageSignatureManifest.model_validate_json(
        signature_manifest.read_text(encoding="utf-8")
    )
    for artifact in manifest.artifacts:
        source = root / artifact.relative_path
        signature = root / artifact.signature_file
        if (
            not source.is_file()
            or not signature.is_file()
            or sha256_file(source) != artifact.sha256
        ):
            raise PackageSignatureError(f"signed artifact mismatch: {artifact.relative_path}")
        with source.open("rb") as stream:
            result = subprocess.run(
                [
                    _ssh_keygen(),
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed),
                    "-I",
                    manifest.signer_identity,
                    "-n",
                    manifest.namespace,
                    "-s",
                    str(signature),
                ],
                stdin=stream,
                capture_output=True,
            )
        if result.returncode != 0:
            raise PackageSignatureError(
                f"OpenSSH signature verification failed: {artifact.relative_path}"
            )
    return manifest
