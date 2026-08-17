"""Per-platform persistence for the one secret store.

The store above this (`secret_store.PlatformSecretStore`) owns the parts that do
not vary: the environment-variable override, the public get/set/clear/status
surface, and the rule that a secret is never returned through ordinary
diagnostics. What varies is only where the bytes actually rest, which is what a
backend answers.

Windows keeps the shipped behaviour exactly: DPAPI-encrypted blobs in a JSON file
under the data directory. macOS uses the login Keychain through the `security`
tool, and Linux uses libsecret through `secret-tool`; both are the platform's own
answer to this problem and neither invents a mux-specific credential format.

The file fallback is **opt-in and off by default**, and that is a deliberate
security stance rather than an oversight. A host with no keyring is a host where
the only thing a fallback can offer is a 0600 file the operator's own account can
read - which is not encryption, however it is encoded. The shipped behaviour when
no backend is available is to refuse persistence and keep working from the
environment variable, because a refused write is recoverable and a silently
cleartext credential is not. `MUX_SECRET_STORE=file` turns it on for an operator
who has decided that trade is right for their host.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Protocol

from .host_platform import IS_LINUX, IS_MACOS, IS_WINDOWS
from .subprocess_flags import background_creation_flags

log = logging.getLogger(__name__)

# Keychain/libsecret record identity. Stable, because changing it orphans every
# secret a user already stored.
_SERVICE = "swe-mux"
_KEYRING_TIMEOUT_SECONDS = 10


class SecretStoreError(RuntimeError):
    pass


class SecretBackend(Protocol):
    """Where secrets rest on this host."""

    @property
    def name(self) -> str:
        """Stable identifier reported by `status()` and the doctor report."""
        ...

    @property
    def encrypted(self) -> bool:
        """Whether the resting form is protected against another local reader."""
        ...

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def clear(self, key: str) -> None: ...


class UnavailableBackend:
    """No persistence on this host. Reads work from the environment; writes refuse."""

    name = "unavailable"
    encrypted = False

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str) -> None:
        raise SecretStoreError(f"persistent secret storage is unavailable: {self._reason}")

    def clear(self, key: str) -> None:
        return None


class DpapiFileBackend:
    """Windows: current-user DPAPI blobs in a JSON file."""

    name = "dpapi"
    encrypted = True

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, key: str) -> str | None:
        from .secret_cipher_windows import dpapi_unprotect

        encoded = _read_file(self.path).get(key)
        if not encoded:
            return None
        try:
            return dpapi_unprotect(base64.b64decode(encoded, validate=True)).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretStoreError("stored secret could not be decrypted") from exc

    def set(self, key: str, value: str) -> None:
        from .secret_cipher_windows import dpapi_protect

        values = _read_file(self.path)
        values[key] = base64.b64encode(dpapi_protect(value.encode("utf-8"))).decode("ascii")
        _write_file(self.path, values)

    def clear(self, key: str) -> None:
        values = _read_file(self.path)
        if key in values:
            del values[key]
            _write_file(self.path, values)


class KeychainBackend:
    """macOS: a generic password per secret in the user's login Keychain."""

    name = "keychain"
    encrypted = True

    def get(self, key: str) -> str | None:
        result = _run(
            ["security", "find-generic-password", "-s", _SERVICE, "-a", key, "-w"],
        )
        if result is None or result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def set(self, key: str, value: str) -> None:
        # -U updates in place instead of appending a duplicate item, which is what
        # makes a re-entered key replace the old one rather than shadow it.
        result = _run(
            ["security", "add-generic-password", "-U", "-s", _SERVICE, "-a", key, "-w", value],
        )
        if result is None or result.returncode != 0:
            raise SecretStoreError("the macOS Keychain refused to store the secret")

    def clear(self, key: str) -> None:
        _run(["security", "delete-generic-password", "-s", _SERVICE, "-a", key])


class SecretToolBackend:
    """Linux: libsecret through `secret-tool`, so the secret lands in the user keyring."""

    name = "libsecret"
    encrypted = True

    def get(self, key: str) -> str | None:
        result = _run(["secret-tool", "lookup", "service", _SERVICE, "account", key])
        if result is None or result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def set(self, key: str, value: str) -> None:
        result = _run(
            [
                "secret-tool",
                "store",
                "--label",
                f"swe-mux {key}",
                "service",
                _SERVICE,
                "account",
                key,
            ],
            stdin=value,
        )
        if result is None or result.returncode != 0:
            raise SecretStoreError("the secret service refused to store the secret")

    def clear(self, key: str) -> None:
        _run(["secret-tool", "clear", "service", _SERVICE, "account", key])


class RestrictedFileBackend:
    """Opt-in fallback: base64 in a 0600 file. Encoded, deliberately not encrypted.

    `encrypted` is False and every surface that reports it says so, because the one
    unacceptable outcome here is a user believing a credential is protected when
    the only thing standing between it and another process is file mode.
    """

    name = "file"
    encrypted = False

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, key: str) -> str | None:
        encoded = _read_file(self.path).get(key)
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SecretStoreError("stored secret could not be decoded") from exc

    def set(self, key: str, value: str) -> None:
        values = _read_file(self.path)
        values[key] = base64.b64encode(value.encode("utf-8")).decode("ascii")
        _write_file(self.path, values, restrict_mode=True)

    def clear(self, key: str) -> None:
        values = _read_file(self.path)
        if key in values:
            del values[key]
            _write_file(self.path, values, restrict_mode=True)


def resolve_backend(path: Path) -> SecretBackend:
    """The secret backend for this host, honouring an explicit `MUX_SECRET_STORE`."""
    override = os.environ.get("MUX_SECRET_STORE", "").strip().casefold()
    if override == "file":
        return RestrictedFileBackend(path)
    if override == "none":
        return UnavailableBackend("disabled by MUX_SECRET_STORE=none")
    if IS_WINDOWS:
        return DpapiFileBackend(path)
    if IS_MACOS:
        if shutil.which("security"):
            return KeychainBackend()
        return UnavailableBackend("the macOS `security` tool was not found")
    if IS_LINUX:
        if shutil.which("secret-tool"):
            return SecretToolBackend()
        return UnavailableBackend(
            "`secret-tool` (libsecret) is not installed; install it, or set "
            "MUX_SECRET_STORE=file to accept an unencrypted 0600 file"
        )
    return UnavailableBackend("no keyring integration for this platform")


def _run(
    command: list[str], *, stdin: str | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a keyring helper. Returns None when it could not be run at all."""
    try:
        return subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_KEYRING_TIMEOUT_SECONDS,
            check=False,
            creationflags=background_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("secret backend command %s failed: %s", command[0], exc)
        return None


def _read_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecretStoreError("encrypted secrets file is unreadable") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SecretStoreError("encrypted secrets file has an unsupported format")
    items = value.get("secrets", {})
    if not isinstance(items, dict):
        raise SecretStoreError("encrypted secrets file is malformed")
    return {str(key): str(item) for key, item in items.items()}


def _write_file(path: Path, values: dict[str, str], *, restrict_mode: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"version": 1, "secrets": values}, separators=(",", ":")),
        encoding="utf-8",
    )
    if restrict_mode:
        # Set the mode on the temp file, before the rename, so the secret is never
        # world-readable even briefly.
        try:
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            log.warning("could not restrict permissions on the secrets file: %s", exc)
    os.replace(temporary, path)


__all__ = [
    "DpapiFileBackend",
    "KeychainBackend",
    "RestrictedFileBackend",
    "SecretBackend",
    "SecretStoreError",
    "SecretToolBackend",
    "UnavailableBackend",
    "resolve_backend",
]
