"""Store or delete one administrator bibliographic API key in the local DPAPI vault."""

from __future__ import annotations

import argparse
import getpass
import json

from app.admin.secrets import AdminBibliographicKeyVault
from app.config import load_settings
from app.corpora import LocalProfile, load_local_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "provider",
        choices=(
            "openalex",
            "elsevier",
            "clarivate",
            "istex",
            "core",
            "semantic_scholar",
            "opencitations",
        ),
    )
    parser.add_argument("--delete", action="store_true")
    arguments = parser.parse_args(argv)
    profile = load_local_profile()
    if profile is not LocalProfile.ADMIN:
        raise PermissionError("CIDERSCHOLAR_LOCAL_PROFILE=admin est requis.")
    vault = AdminBibliographicKeyVault(load_settings(), profile)
    if arguments.delete:
        vault.delete(arguments.provider)
    else:
        vault.save(
            arguments.provider,
            getpass.getpass(f"Clé {arguments.provider} (saisie masquée) : "),
        )
    print(json.dumps({"provider": arguments.provider, "configured": not arguments.delete}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
