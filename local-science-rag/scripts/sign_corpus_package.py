"""Sign an immutable corpus package with an administrator OpenSSH Ed25519 key."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.corpus_packages.signatures import sign_corpus_package


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_directory", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = sign_corpus_package(
        args.version_directory,
        private_key=args.private_key,
        signer_identity=args.identity,
    )
    print(manifest.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
