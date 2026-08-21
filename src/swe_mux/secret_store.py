from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from .secret_backends import SecretBackend, SecretStoreError, resolve_backend

__all__ = ["PlatformSecretStore", "SecretStore", "SecretStoreError"]


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def clear(self, name: str) -> None: ...

    def status(self, name: str) -> dict[str, object]: ...


class PlatformSecretStore:
    """Write-only-at-the-API secret store over the host's own credential storage.

    The environment override, the never-return-a-secret-through-diagnostics rule,
    and the status shape are the same on every host; only where the bytes rest
    differs, and that is `secret_backends.resolve_backend`'s answer.
    """

    ENV_NAMES = {
        "openrouter_api_key": "OPENROUTER_API_KEY",
        # A custom endpoint frequently needs no key at all (llama.cpp and Ollama
        # serve unauthenticated), so this is an override for the case where it
        # does and the operator would rather keep it out of the credential store.
        "custom_llm_api_key": "SWE_MUX_CUSTOM_LLM_API_KEY",
    }

    def __init__(self, path: Path, backend: SecretBackend | None = None) -> None:
        self.path = path
        self._backend = backend if backend is not None else resolve_backend(path)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def get(self, name: str) -> str | None:
        env_name = self.ENV_NAMES.get(name)
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
        return self._backend.get(name)

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise SecretStoreError("secret must not be empty")
        self._backend.set(name, value)

    def clear(self, name: str) -> None:
        self._backend.clear(name)

    def status(self, name: str) -> dict[str, object]:
        """Whether a secret is configured, and how well it is protected at rest.

        `encrypted` is reported separately from `persistent` because the two come
        apart on a host using the opt-in file fallback: the secret survives a
        restart while being protected by nothing stronger than file mode, and a
        caller that reads only `persistent` would present that as equivalent to
        DPAPI or a Keychain entry.
        """
        env_name = self.ENV_NAMES.get(name)
        if env_name and os.environ.get(env_name):
            return {
                "configured": True,
                "source": "environment",
                "persistent": False,
                "encrypted": False,
                "backend": "environment",
            }
        try:
            configured = self._backend.get(name) is not None
        except SecretStoreError:
            return {
                "configured": False,
                "source": "error",
                "persistent": False,
                "encrypted": False,
                "backend": self._backend.name,
            }
        return {
            "configured": configured,
            # `source` keeps its shipped vocabulary (stored/environment/none/error).
            # Which backend stored it is the new `backend` key, so an existing
            # reader is unaffected by gaining a second credential store.
            "source": "stored" if configured else "none",
            "persistent": configured,
            "encrypted": configured and self._backend.encrypted,
            "backend": self._backend.name,
        }
