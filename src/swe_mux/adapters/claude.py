from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

from .base import SpawnOptions, SpawnSpec


def encode_cwd(cwd: Path | str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd).resolve()))


def is_conversation_transcript(path: Path) -> bool:
    """True when the file name is a plain conversation id, not CLI housekeeping.

    Claude Code renames a session file it can no longer attribute to
    ``<id>.orphaned-<ts>-<hash>.jsonl``. Its stem still *starts* with the original
    conversation id, so anything deriving a native id from the stem maps the
    fragment onto the real conversation's history row: the two files then alternate
    ownership of one per-path watermark and both re-parse on every startup, with a
    stale fragment shown as the conversation. A dot inside the stem is the exact
    discriminator — a conversation id never contains one.
    """
    return "." not in path.stem


def claude_data_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _bash_executable_path(executable: str) -> str:
    """Translate a Windows executable path for Claude's Bash hook runner."""
    normalized = executable.replace("\\", "/")
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"/{normalized[0].lower()}{normalized[2:]}"
    return normalized


def _hook_command(event: str, executable: str | None = None) -> str:
    python = _bash_executable_path(executable or sys.executable)
    return shlex.join([python, "-m", "swe_mux.hook_client", event])


class ClaudeAdapter:
    name = "claude"

    def __init__(
        self,
        default_exe: str = "claude.exe",
        data_dir: Path | None = None,
        default_args: list[str] | None = None,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self.data_dir = data_dir
        self.settings_path = self._write_hook_settings(data_dir) if data_dir else None

    def _write_hook_settings(self, data_dir: Path) -> Path:
        path = data_dir / "claude-hooks.json"
        hooks: dict[str, list[dict[str, object]]] = {}
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "PermissionRequest",
            "Notification",
            "Stop",
            "SessionEnd",
        ):
            command = _hook_command(event)
            hooks[event] = [{"hooks": [{"type": "command", "command": command}]}]
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"hooks": hooks}, indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def _session_settings(self, session_id: str | None) -> Path | None:
        if not self.data_dir or not session_id:
            return self.settings_path
        path = self.data_dir / "sessions" / session_id / "claude-hooks.json"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            generated = self._write_hook_settings(path.parent)
            if generated != path:
                generated.replace(path)
        return path

    def _args(self, action: str, native_id: str, opts: SpawnOptions) -> list[str]:
        args = [action, native_id]
        settings = self._session_settings(opts.session_id)
        if settings:
            args.extend(["--settings", str(settings)])
        return [*args, *self.default_args, *opts.args]

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        return SpawnSpec(
            opts.exe or self.default_exe,
            tuple(self._args("--session-id", sid, opts)),
        )

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        return SpawnSpec(
            opts.exe or self.default_exe,
            tuple(self._args("--resume", native_id, opts)),
        )

    def transcript_path(self, native_id: str, cwd: Path) -> Path:
        return claude_data_home() / "projects" / encode_cwd(cwd) / f"{native_id}.jsonl"

    def graceful_exit_keys(self) -> str:
        return "/exit\r"

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        root = claude_data_home() / "projects" / encode_cwd(cwd)
        if not root.exists():
            return []
        recent: list[tuple[float, Path, str]] = []
        for path in root.glob("*.jsonl"):
            if not is_conversation_transcript(path):
                continue
            # Claude's own startup cleanup deletes stale transcripts in this very
            # directory, so a file can vanish between glob and stat. One raced
            # candidate must not kill the caller's detection loop.
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified + 2 >= created_at:
                recent.append((modified, path, path.stem))
        return recent

    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None:
        del created_at
        path = self.transcript_path(native_id, cwd)
        while not stop.is_set() and not path.exists():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except TimeoutError:
                pass
        return path if path.exists() else None

    def transcript_native_id(self, path: Path) -> str:
        return path.stem

    def cleanup(self, session_id: str) -> None:
        if self.data_dir:
            shutil.rmtree(self.data_dir / "sessions" / session_id, ignore_errors=True)

    def session_env(self, session_id: str) -> dict[str, str]:
        settings = self._session_settings(session_id)
        return {"MUX_CLAUDE_SETTINGS": str(settings)} if settings else {}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        self.default_args = list(args)

    def media_reference(self, path: Path) -> str:
        return str(path)
