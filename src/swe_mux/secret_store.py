from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def clear(self, name: str) -> None: ...

    def status(self, name: str) -> dict[str, object]: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def _dpapi_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("persistent secret storage is unavailable on this platform")
    source, keepalive = _blob(value)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "swe-mux", None, None, None, 0, ctypes.byref(result)
    ):
        raise SecretStoreError(f"DPAPI protection failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
        del keepalive


def _dpapi_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("persistent secret storage is unavailable on this platform")
    source, keepalive = _blob(value)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise SecretStoreError(f"DPAPI decryption failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
        del keepalive


class PlatformSecretStore:
    """Write-only-at-the-API secret store with a Windows current-user DPAPI backend."""

    ENV_NAMES = {"openrouter_api_key": "OPENROUTER_API_KEY"}

    def __init__(self, path: Path) -> None:
        self.path = path

    def _stored(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretStoreError("encrypted secrets file is unreadable") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise SecretStoreError("encrypted secrets file has an unsupported format")
        items = value.get("secrets", {})
        if not isinstance(items, dict):
            raise SecretStoreError("encrypted secrets file is malformed")
        return {str(key): str(item) for key, item in items.items()}

    def _write(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "secrets": values}, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def get(self, name: str) -> str | None:
        env_name = self.ENV_NAMES.get(name)
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
        encoded = self._stored().get(name)
        if not encoded:
            return None
        try:
            return _dpapi_unprotect(base64.b64decode(encoded, validate=True)).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretStoreError("stored secret could not be decrypted") from exc

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise SecretStoreError("secret must not be empty")
        values = self._stored()
        values[name] = base64.b64encode(_dpapi_protect(value.encode("utf-8"))).decode("ascii")
        self._write(values)

    def clear(self, name: str) -> None:
        values = self._stored()
        if name in values:
            del values[name]
            self._write(values)

    def status(self, name: str) -> dict[str, object]:
        env_name = self.ENV_NAMES.get(name)
        if env_name and os.environ.get(env_name):
            return {"configured": True, "source": "environment", "persistent": False}
        try:
            configured = bool(self._stored().get(name))
            return {
                "configured": configured,
                "source": "stored" if configured else "none",
                "persistent": configured,
            }
        except SecretStoreError:
            return {"configured": False, "source": "error", "persistent": False}
