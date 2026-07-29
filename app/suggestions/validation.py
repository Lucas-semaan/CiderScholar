"""Network-free DOI, URL and PDF validation for suggestion inputs."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app.updates.models import DOI_PATTERN


class SuggestionValidationError(ValueError):
    """A suggestion input is unsafe or malformed."""


def normalize_suggestion_doi(value: str) -> str:
    cleaned = value.strip()
    lowered = cleaned.casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    cleaned = cleaned.rstrip(".,;")
    if DOI_PATTERN.fullmatch(cleaned) is None:
        raise SuggestionValidationError("DOI invalide.")
    return cleaned.lower()


def validate_reference_url(value: str) -> str:
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise SuggestionValidationError("L'URL doit utiliser HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise SuggestionValidationError("Les identifiants dans une URL sont interdits.")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise SuggestionValidationError("Les hôtes locaux sont interdits.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise SuggestionValidationError("Les adresses réseau non publiques sont interdites.")
    if parsed.port not in {None, 443}:
        raise SuggestionValidationError("Seul le port HTTPS standard est autorisé.")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, parsed.fragment))


def validate_pdf_payload(
    filename: str,
    payload: bytes,
    *,
    maximum_bytes: int,
) -> tuple[str, str]:
    if Path(filename).suffix.casefold() != ".pdf":
        raise SuggestionValidationError("Le fichier doit porter l'extension .pdf.")
    if not payload.startswith(b"%PDF-"):
        raise SuggestionValidationError("Le fichier ne possède pas une signature PDF valide.")
    if not payload or len(payload) > maximum_bytes:
        raise SuggestionValidationError(
            f"Le PDF dépasse la taille maximale de {maximum_bytes} octets."
        )
    return f"suggestion-{uuid4().hex}.pdf", hashlib.sha256(payload).hexdigest()


def canonical_package_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, payload in sorted(files.items()):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", name):
            raise SuggestionValidationError("Nom de fichier interne invalide.")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()
