from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..harness import descriptor
from ..opencode_store import session_measurements
from .base import BackendAdapter, SpawnOptions, SpawnSpec


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _model_json(value: Any) -> dict[str, Any]:
    """opencode stores the model as a JSON blob: {id, providerID, variant}."""
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _model_id(value: Any) -> str | None:
    model = _model_json(value).get("id")
    return model if isinstance(model, str) and model else None


def _provider_id(value: Any) -> str | None:
    provider = _model_json(value).get("providerID")
    return provider if isinstance(provider, str) and provider else None


def opencode_plugin_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "opencode_mux_plugin.js"


def materialize_mux_config(
    root: Path,
    plugin_source: Path,
    *,
    mcp_url: str | None = None,
    mcp_token: str | None = None,
) -> Path:
    """Write the per-session opencode config that loads the mux plugin.

    opencode merges its configuration layers rather than replacing them, and
    ``OPENCODE_CONFIG`` names one additional *file*. Pointing it at a mux-owned
    JSON that lists the plugin by absolute path therefore adds the plugin while
    the user's own ``~/.config/opencode/opencode.jsonc``, project
    ``opencode.json``, and ``.opencode/`` directories all keep loading.

    This is deliberately not ``OPENCODE_CONFIG_DIR``. That variable *replaces*
    the config directory, so using it means mirroring the user's agents,
    commands, modes, plugins, and auth into an overlay and keeping the mirror
    honest — on Windows that means directory junctions, which is how Orca hit a
    teardown that walked a junction out of its own overlay. Verified against
    opencode 1.18.16 (2026-08-10): a plugin named by absolute path in an
    ``OPENCODE_CONFIG`` file loads and receives the full event bus.
    """
    root.mkdir(parents=True, exist_ok=True)
    plugin = root / "mux-plugin.js"
    staged_plugin = root / "mux-plugin.js.tmp"
    staged_plugin.write_bytes(plugin_source.read_bytes())
    staged_plugin.replace(plugin)

    payload: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        # Forward slashes: the value is read as a module specifier, and a
        # Windows backslash would be consumed as a JSON escape.
        "plugin": [plugin.as_posix()],
    }
    if mcp_url and mcp_token:
        # A literal token, in a session-private file, exactly as the omp
        # extension package carries one: mux mints a distinct MCP token per
        # session, so a shared registration would either fail authentication or
        # hand one session's identity to another. opencode's `{env:VAR}`
        # interpolation is deliberately not used for the same reason — the value
        # would resolve from whatever environment the process happens to carry.
        payload["mcp"] = {
            "mux": {
                "type": "remote",
                "url": mcp_url,
                "enabled": True,
                "headers": {"Authorization": f"Bearer {mcp_token}"},
            }
        }
    config = root / "opencode.json"
    staged_config = root / "opencode.json.tmp"
    staged_config.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    staged_config.replace(config)
    try:
        config.chmod(0o600)
    except OSError:
        pass
    return config


def retire_mux_config(root: Path, session_id: str) -> None:
    source = root / session_id
    if not source.exists():
        return
    trash = root.parent / ".trash" / "opencode-configs"
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / session_id
    suffix = 1
    while destination.exists():
        destination = trash / f"{session_id}-{suffix}"
        suffix += 1
    shutil.move(str(source), str(destination))


class OpenCodeAdapter(BackendAdapter):
    """Adapter for opencode's TUI.

    opencode is a client/server pair in one process: the `opencode` command
    starts a server on a random loopback port and attaches its TUI to it. mux
    does not pin that port. The plugin runs inside the server and is handed its
    own ``serverUrl``, so it reports the address mux would otherwise have to
    reserve, which avoids both a port collision and a startup race.

    Conversations are rows in ``opencode.db``, not files, so every transcript
    method here answers ``None``. That is the honest answer rather than a
    degraded one: the descriptor declares no transcript and no measurement
    source, so nothing asks this adapter to produce a path it cannot have.
    """

    name = "opencode"
    reports_conversation_rollover = True
    assigns_conversation_id = False
    resolves_transcript_by_cwd = False

    def __init__(
        self,
        default_exe: str = "opencode",
        default_args: list[str] | None = None,
        *,
        data_home: Path | None = None,
        data_dir: Path | None = None,
        mcp_url: str | None = None,
        command_resolver: Callable[[str], tuple[str, tuple[str, ...]]] | None = None,
        instrument: bool = True,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self._data_home = data_home
        self._config_root = data_dir / "opencode-configs" if data_dir else None
        self._mcp_url = mcp_url
        # "Launch clean": the opencode config carries both the plugin (lifecycle
        # hook) and the MCP registration, so dropping it runs this harness unobserved.
        self.instrument = instrument
        self._context_windows: dict[tuple[str, str], int] | None = None
        # npm installs opencode as a `.cmd` batch shim wrapping the real
        # platform binary, and ConPTY cannot execute a batch shim.
        self.command_resolver = command_resolver

    def _resolve(self, executable: str) -> tuple[str, tuple[str, ...]]:
        return self.command_resolver(executable) if self.command_resolver else (executable, ())

    @property
    def data_home(self) -> Path:
        return self._data_home or descriptor("opencode").data_home()

    def database_path(self) -> Path:
        """Where opencode keeps sessions, messages, parts, and its event log."""
        return self.data_home / "opencode.db"

    def session_measurements(self, native_id: str) -> dict[str, Any] | None:
        """Exact token, cost, and model figures for one conversation.

        Delegated to `opencode_store`, which owns every read of this database: the
        retroactive discovery path needs the same figures for a conversation mux
        never ran, and two readers of one table is how they come to disagree.
        """
        return session_measurements(self.database_path(), native_id)

    # ------------------------------------------------------------------ launch

    def _session_config(self, session_id: str, mcp_token: str | None = None) -> Path | None:
        if self._config_root is None or not self.instrument:
            return None
        try:
            return materialize_mux_config(
                self._config_root / session_id,
                opencode_plugin_path(),
                mcp_url=self._mcp_url,
                mcp_token=mcp_token,
            )
        except OSError:
            # Forfeit the plugin rather than the pane. Without the config the
            # session still runs, still renders, and still accepts input; it
            # simply reports no lifecycle hooks.
            return None

    def _env(self, session_id: str, mcp_token: str | None = None) -> dict[str, str]:
        config = self._session_config(session_id, mcp_token)
        return {"OPENCODE_CONFIG": str(config)} if config is not None else {}

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        executable, prefix = self._resolve(opts.exe or self.default_exe)
        return SpawnSpec(
            executable,
            (*prefix, *self.default_args, *opts.args),
            self._env(opts.session_id or sid, opts.mcp_token),
        )

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        executable, prefix = self._resolve(opts.exe or self.default_exe)
        return SpawnSpec(
            executable,
            (*prefix, "--session", native_id, *self.default_args, *opts.args),
            self._env(opts.session_id or native_id, opts.mcp_token),
        )

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        # A session is addressed by id and carries its own `directory` column, so
        # `--session` reopens the same conversation wherever the pane runs.
        del recorded_cwd, target_cwd
        return True

    def graceful_exit_keys(self) -> str:
        # opencode's TUI leaves on Ctrl+C; the trailing Ctrl+D covers a composer
        # that swallowed the interrupt as "clear the draft".
        return "\x03\x04"

    def session_env(self, session_id: str) -> Mapping[str, str]:
        del session_id
        return {}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        self.default_args = list(args)

    def cleanup(self, session_id: str) -> None:
        if self._config_root is None:
            return
        retire_mux_config(self._config_root, session_id)

    def media_reference(self, path: Path) -> str:
        return str(path)

    # -------------------------------------------------------------- transcript

    def transcript_path(self, native_id: str, cwd: Path) -> Path | None:
        del native_id, cwd
        return None

    def locate_transcript(self, native_id: str) -> Path | None:
        del native_id
        return None

    def pending_transcript_path(self, session_id: str, current: Path) -> Path | None:
        del session_id, current
        return None

    def transcript_native_id(self, path: Path) -> str | None:
        del path
        return None

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        del cwd, created_at
        return []

    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None:
        del native_id, cwd, created_at, stop
        return None

    def catalog_path(self) -> Path:
        """opencode's own cached model catalogue (models.dev data).

        Read from opencode's cache rather than shared with another harness's
        catalogue on purpose. The two genuinely disagree: for `openai/gpt-5.6-sol`
        opencode records a 1,050,000-token context where pi's catalogue records
        272,000. Each harness routes through its own provider stack, so its own
        view of the window is the correct one for its sessions, and borrowing a
        neighbour's would render a context percentage wrong by ~4x.
        """
        cache = os.environ.get("XDG_CACHE_HOME")
        root = Path(cache).expanduser() if cache else Path.home() / ".cache"
        return root / "opencode" / "models.json"

    def model_context_window(self, provider: str, model: str) -> int:
        if self._context_windows is None:
            self._context_windows = self._load_context_windows()
        return self._context_windows.get((provider, model), 0)

    def _load_context_windows(self) -> dict[tuple[str, str], int]:
        """Parse `{provider: {models: {id: {limit: {context: N}}}}}`.

        An unreadable or unknown entry yields no window, and the caller leaves
        context unreported rather than publishing 0% — which the codebase records
        as indistinguishable from a fresh conversation.
        """
        try:
            catalog = json.loads(self.catalog_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(catalog, dict):
            return {}
        windows: dict[tuple[str, str], int] = {}
        for provider_id, provider in catalog.items():
            if not isinstance(provider_id, str) or not isinstance(provider, dict):
                continue
            models = provider.get("models")
            if not isinstance(models, dict):
                continue
            for model_id, model in models.items():
                if not isinstance(model_id, str) or not isinstance(model, dict):
                    continue
                limit = model.get("limit")
                context = limit.get("context") if isinstance(limit, dict) else None
                if isinstance(context, int) and not isinstance(context, bool) and context > 0:
                    windows[(provider_id, model_id)] = context
        return windows


__all__ = [
    "OpenCodeAdapter",
    "materialize_mux_config",
    "opencode_plugin_path",
    "retire_mux_config",
]
