"""Load configured secrets without ever returning or logging their values."""

from __future__ import annotations

import base64
import ctypes
import os
import re
import tempfile
from collections.abc import Iterable
from ctypes import wintypes
from pathlib import Path
from typing import Protocol, runtime_checkable

try:
    import winreg
except ImportError:  # pragma: no cover - unavailable outside Windows
    winreg = None  # type: ignore[assignment]


SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
DPAPI_PREFIX = "dpapi-v1:"
CRYPTPROTECT_UI_FORBIDDEN = 0x1
MAX_PROTECTED_SECRET_BYTES = 64 * 1024


@runtime_checkable
class LocalSecretStore(Protocol):
    """Framework-independent persistence contract for one current-user secret."""

    def configured(self) -> bool: ...

    def save(self, secret: str) -> None: ...

    def load(self) -> str | None: ...

    def delete(self) -> None: ...


class DpapiFileSecretStore:
    """Persist one current-user DPAPI ciphertext in an atomic versioned file."""

    def __init__(self, path: Path, *, description: str) -> None:
        self.path = path.resolve()
        self.description = description

    def configured(self) -> bool:
        return self.path.is_file()

    def save(self, secret: str) -> None:
        if not secret:
            raise ValueError("secret cannot be empty")
        protected = _protect_windows_data(
            secret.encode("utf-8"),
            description=self.description,
        )
        encoded = DPAPI_PREFIX + base64.urlsafe_b64encode(protected).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="ascii",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        if not self.path.is_file() or self.path.stat().st_size > MAX_PROTECTED_SECRET_BYTES:
            raise RuntimeError("protected secret file is invalid")
        value = self.path.read_text(encoding="ascii")
        if not value.startswith(DPAPI_PREFIX):
            raise RuntimeError("protected secret file has an unsupported version")
        try:
            protected = base64.b64decode(
                value.removeprefix(DPAPI_PREFIX).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            return _unprotect_windows_data(protected).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("protected secret file could not be decrypted") from exc

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _validate_variable_name(name: str) -> None:
    if not SECRET_NAME.fullmatch(name):
        raise ValueError("secret environment variable name is invalid")


def _crypt32() -> tuple[object, object]:
    if os.name != "nt":
        raise RuntimeError("DPAPI credential persistence is available only on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _input_blob(payload: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(payload)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    return _DataBlob(len(payload), pointer), buffer


def _protect_windows_data(
    payload: bytes,
    *,
    description: str = "CiderScholar publisher credential",
) -> bytes:
    crypt32, kernel32 = _crypt32()
    source, source_buffer = _input_blob(payload)
    destination = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        description,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(destination),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        del source_buffer
        kernel32.LocalFree(ctypes.cast(destination.pbData, wintypes.HLOCAL))


def _unprotect_windows_data(payload: bytes) -> bytes:
    crypt32, kernel32 = _crypt32()
    source, source_buffer = _input_blob(payload)
    destination = _DataBlob()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(destination),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        del source_buffer
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
        kernel32.LocalFree(ctypes.cast(destination.pbData, wintypes.HLOCAL))


def hydrate_user_environment(variable_names: Iterable[str]) -> None:
    """Refresh missing process variables from the Windows user environment.

    Windows processes inherit a snapshot of their parent's environment. A long-lived
    launcher may therefore miss variables added later with ``SetEnvironmentVariable``.
    The user environment registry is the source Windows itself uses for new sessions.
    """

    if winreg is None:
        return
    missing = {name for name in variable_names if name and not os.environ.get(name)}
    if not missing:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment_key:
            for name in missing:
                try:
                    value, _ = winreg.QueryValueEx(environment_key, name)
                except FileNotFoundError:
                    continue
                if isinstance(value, str) and value.strip():
                    os.environ[name] = value.strip()
    except OSError:
        # A missing or inaccessible user environment must not prevent local startup.
        return


def persist_user_environment_value(name: str, value: str) -> None:
    """Persist a non-empty value for the current Windows user and current process."""

    _validate_variable_name(name)
    if winreg is None:
        raise RuntimeError("persistent user secrets are available only on Windows")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("persisted value cannot be empty")
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as environment_key:
        winreg.SetValueEx(environment_key, name, 0, winreg.REG_SZ, cleaned)
    os.environ[name] = cleaned


def persist_protected_user_secret(name: str, secret: str) -> None:
    """Protect a secret with current-user DPAPI before persisting its ciphertext."""

    if not secret:
        raise ValueError("secret cannot be empty")
    protected = _protect_windows_data(secret.encode("utf-8"))
    encoded = DPAPI_PREFIX + base64.urlsafe_b64encode(protected).decode("ascii")
    persist_user_environment_value(name, encoded)


def load_protected_user_secret(name: str) -> str | None:
    _validate_variable_name(name)
    value = os.environ.get(name)
    if not value:
        hydrate_user_environment([name])
        value = os.environ.get(name)
    if not value:
        return None
    if not value.startswith(DPAPI_PREFIX):
        raise RuntimeError("publisher password is not stored as a DPAPI-protected value")
    try:
        protected = base64.urlsafe_b64decode(value.removeprefix(DPAPI_PREFIX).encode("ascii"))
        return _unprotect_windows_data(protected).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("publisher password could not be decrypted") from exc


def delete_user_environment_value(name: str) -> None:
    _validate_variable_name(name)
    if winreg is None:
        raise RuntimeError("persistent user secrets are available only on Windows")
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as environment_key:
            winreg.DeleteValue(environment_key, name)
    except FileNotFoundError:
        pass
    os.environ.pop(name, None)
