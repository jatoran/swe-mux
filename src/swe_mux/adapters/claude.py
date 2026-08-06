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
    # Spawned with an injected `--session-id` and per-session hook settings, so
    # every conversation replacement (`/clear`, in-CLI `/resume`) is reported by
    # the CLI itself via the SessionStart ingress. Rollovers are hook-driven;
    # the transcript-switch heuristic must never move a Claude session.
    reports_conversation_rollover = True
    # mux spawns with `--session-id <mux id>`, so the CLI writes exactly that
    # conversation and native_session_id is authoritative from the first moment.
    assigns_conversation_id = True
    # Transcripts live under `projects/<encoded cwd>/`, so a cwd change *moves the
    # existing file*. Entering a Claude native worktree is exactly that, and it is
    # why a live session's transcript path is never permanently true.
    resolves_transcript_by_cwd = True

    def __init__(
        self,
        default_exe: str = "claude.exe",
        data_dir: Path | None = None,
        default_args: list[str] | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self.data_dir = data_dir
        self.settings_path = self._write_hook_settings(data_dir) if data_dir else None
        self.mcp_config_path = (
            self._write_mcp_config(data_dir, mcp_url) if data_dir and mcp_url else None
        )

    @staticmethod
    def _write_mcp_config(data_dir: Path, mcp_url: str) -> Path:
        """One static registration file for the mux MCP server (`--mcp-config`).

        The URL is literal (stable daemon port); only the bearer token is
        per-session, carried by `${MUX_MCP_TOKEN}` env expansion so no
        per-session file is needed. `--mcp-config` *adds* servers, so a user's
        own MCP configuration is untouched.
        """
        path = data_dir / "claude-mcp.json"
        payload = {
            "mcpServers": {
                "mux": {
                    "type": "http",
                    "url": mcp_url,
                    "headers": {"Authorization": "Bearer ${MUX_MCP_TOKEN}"},
                }
            }
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

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
            # Subagent lifecycle drives the `subagents` standing-activity count
            # (starts − stops); sidechain transcript records are the fallback
            # tier when these are lost. They arrive subagent-scoped and carry
            # the root session_id, so the foreign-conversation filter composes.
            "SubagentStart",
            "SubagentStop",
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
        # `--mcp-config` is a VARIADIC option in the Claude CLI: it keeps
        # consuming following tokens until the next flag. It must therefore
        # never sit immediately before the trailing positional args — with a
        # seed prompt appended there, the CLI read the prompt as a second MCP
        # config path and exited ("MCP config file not found: <prompt>"). The
        # single-value `--settings` is what may safely precede positionals.
        args = [action, native_id]
        if self.mcp_config_path:
            args.extend(["--mcp-config", str(self.mcp_config_path)])
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

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        """`--resume` continues the conversation only in the directory that owns it.

        Claude keeps transcripts in a per-project directory derived from the encoded
        working directory, so resuming the same id under a different root writes a
        different file: a different conversation, and its own history entry.

        Compared through the CLI's own encoding rather than a generic path test,
        because the encoding is the rule the CLI actually applies — two roots that
        encode identically resolve to one transcript directory. Case-folded on top of
        it, since that directory lives on this platform's filesystem and Windows
        would hand both spellings the same folder.
        """
        try:
            return os.path.normcase(encode_cwd(recorded_cwd)) == os.path.normcase(
                encode_cwd(target_cwd)
            )
        except OSError:
            return False

    def transcript_path(self, native_id: str, cwd: Path) -> Path:
        return claude_data_home() / "projects" / encode_cwd(cwd) / f"{native_id}.jsonl"

    def locate_transcript(self, native_id: str) -> Path | None:
        """Find `<native_id>.jsonl` in whichever project directory now holds it.

        The CLI names a conversation's file after the conversation id, and mux
        dictates that id at spawn (`--session-id`), so this is a search for a file
        whose name we own outright: it cannot land on a sibling's conversation the
        way an mtime scan can. That makes it safe to use as a recovery path where
        the `(native id, cwd)` computation has stopped being true — after a cwd
        change moved the file, or across a daemon restart that re-derives the path
        from a spawn cwd the CLI has since left.

        A directory probe per project slug rather than a recursive walk: measured
        2026-08-06 against a real tree of 448 project directories, 9 ms and exactly
        one hit. Cheap enough for a fallback, too expensive for a poll loop, which
        is why callers throttle it.

        Two files answering to one conversation id is not a case Claude produces —
        it *moves* the file rather than copying it — but if it ever happens the
        largest wins, because a conversation only grows and the live file is
        therefore the longest. Deliberately not the newest mtime: Windows leaves an
        open file's timestamp frozen at creation, so "newest" can name the dead one.
        """
        if not native_id:
            return None
        root = claude_data_home() / "projects"
        name = f"{native_id}.jsonl"
        found: list[tuple[int, Path]] = []
        try:
            entries = list(os.scandir(root))
        except OSError:
            return None
        for entry in entries:
            candidate = Path(entry.path) / name
            try:
                size = candidate.stat().st_size
            except OSError:
                # Not a directory, or no such conversation here. Either way this
                # slug does not hold the file, and one raced or locked entry must
                # not abort the search.
                continue
            found.append((size, candidate))
        if not found:
            return None
        return max(found)[1]

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
