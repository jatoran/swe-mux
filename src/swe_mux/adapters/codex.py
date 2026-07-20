from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from .base import SpawnOptions, SpawnSpec


def codex_data_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


class CodexAdapter:
    name = "codex"

    def __init__(
        self,
        default_exe: str = "codex.exe",
        notify: bool = False,
        default_args: list[str] | None = None,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self.notify_program = (
            [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"] if notify else None
        )

    def _args(self, args: list[str]) -> list[str]:
        if not self.notify_program:
            return [*self.default_args, *args]
        return ["-c", f"notify={json.dumps(self.notify_program)}", *self.default_args, *args]

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        del sid
        return SpawnSpec(opts.exe or self.default_exe, tuple(self._args(opts.args)))

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        return SpawnSpec(
            opts.exe or self.default_exe,
            tuple(self._args(["resume", native_id, *opts.args])),
        )

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

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        root = codex_data_home() / "sessions"
        if not root.exists():
            return []
        resolved = str(cwd.resolve()).casefold()
        result: list[tuple[float, Path, str]] = []
        paths = sorted(
            root.glob("**/rollout-*.jsonl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:20]
        for path in paths:
            modified = path.stat().st_mtime
            association = self._association(path)
            if modified + 2 >= created_at and association and association[1].casefold() == resolved:
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
