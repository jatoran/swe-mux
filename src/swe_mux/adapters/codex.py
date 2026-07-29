from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..codex_tui import with_scrollback_safe_tui
from .base import SpawnOptions, SpawnSpec

# Shared across every live Codex session: they all scan the same tree on the same
# cadence, so one pass per window instead of one per session per tick.
_ROLLOUT_CACHE_SECONDS = 2.0
_ROLLOUT_CACHE: dict[str, tuple[float, list[tuple[float, Path]]]] = {}


def codex_data_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


class CodexAdapter:
    name = "codex"
    # Codex has no session-start hook, so a `/new` under a live PTY is only
    # discoverable from the filesystem; the transcript-switch heuristic stays on.
    reports_conversation_rollover = False

    def __init__(
        self,
        default_exe: str = "codex.exe",
        notify: bool = False,
        default_args: list[str] | None = None,
        command_resolver: Callable[[str], tuple[str, tuple[str, ...]]] | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self.command_resolver = command_resolver
        self.notify_program = (
            [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"] if notify else None
        )
        self.mcp_url = mcp_url

    def _mcp_args(self) -> list[str]:
        """Register the mux MCP server as streamable HTTP (Codex >= 0.145).

        Values are explicit TOML strings; the bearer comes from the session env
        (`bearer_token_env_var`), so the argv itself carries no secret.
        """
        if not self.mcp_url:
            return []
        return [
            "-c",
            f'mcp_servers.mux.url="{self.mcp_url}"',
            "-c",
            'mcp_servers.mux.bearer_token_env_var="MUX_MCP_TOKEN"',
        ]

    def _args(self, args: list[str]) -> list[str]:
        configured = with_scrollback_safe_tui([*self.default_args, *args])
        prefix = self._mcp_args()
        if self.notify_program:
            prefix = ["-c", f"notify={json.dumps(self.notify_program)}", *prefix]
        return [*prefix, *configured]

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        del sid
        executable = opts.exe or self.default_exe
        resolved, prefix = (
            self.command_resolver(executable) if self.command_resolver else (executable, ())
        )
        return SpawnSpec(resolved, (*prefix, *self._args(opts.args)))

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        executable = opts.exe or self.default_exe
        resolved, prefix = (
            self.command_resolver(executable) if self.command_resolver else (executable, ())
        )
        return SpawnSpec(resolved, (*prefix, *self._args(["resume", native_id, *opts.args])))

    def transcript_path(self, native_id: str, cwd: Path) -> None:
        del native_id, cwd
        return None

    def graceful_exit_keys(self) -> str:
        return "/exit\r"

    def _association(self, path: Path) -> tuple[str, str] | None:
        try:
            first = json.loads(path.open("r", encoding="utf-8", errors="replace").readline())
            payload = first.get("payload") or {}
            if payload.get("parent_thread_id"):
                return None
            if payload.get("id") and payload.get("cwd"):
                return str(payload["id"]), str(Path(payload["cwd"]).resolve())
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _newest_rollouts(self, root: Path) -> list[tuple[float, Path]]:
        """The 20 newest rollout files, cached briefly and shared across sessions.

        Codex stores rollouts in a flat date tree that mux never prunes, and this
        runs on a 2-second switch-watch tick *per live Codex session*. Re-globbing
        and re-stat-ing thousands of files per tick, forever, was tens of
        thousands of stat calls a second on an install with real history — pure
        waste whenever no switch is happening. The window is short enough that a
        genuine in-CLI resume is still noticed within one extra tick.
        """
        now = time.monotonic()
        key = str(root)
        cached = _ROLLOUT_CACHE.get(key)
        if cached and now - cached[0] < _ROLLOUT_CACHE_SECONDS:
            return cached[1]
        found: list[tuple[float, Path]] = []
        for path in root.glob("**/rollout-*.jsonl"):
            try:
                found.append((path.stat().st_mtime, path))
            except OSError:
                continue
        found.sort(key=lambda item: item[0], reverse=True)
        newest = found[:20]
        _ROLLOUT_CACHE[key] = (now, newest)
        return newest

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        root = codex_data_home() / "sessions"
        if not root.exists():
            return []
        resolved = str(cwd.resolve()).casefold()
        result: list[tuple[float, Path, str]] = []
        for modified, path in self._newest_rollouts(root):
            if modified + 2 < created_at:
                continue
            association = self._association(path)
            if association and association[1].casefold() == resolved:
                result.append((modified, path, association[0]))
        return result

    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None:
        while not stop.is_set():
            candidates = self.recent_transcripts(cwd, created_at)
            exact = [item for item in candidates if item[2] == native_id]
            if exact:
                return max(exact)[1]
            if candidates:
                return max(candidates)[1]
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except TimeoutError:
                pass
        return None

    def transcript_native_id(self, path: Path) -> str | None:
        association = self._association(path)
        return association[0] if association else None

    def cleanup(self, session_id: str) -> None:
        del session_id

    def session_env(self, session_id: str) -> dict[str, str]:
        del session_id
        return {}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        self.default_args = list(args)

    def media_reference(self, path: Path) -> str:
        return str(path)
