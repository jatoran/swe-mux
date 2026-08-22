from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import shlex
import shutil
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from ..approvals import DECISION_HOOK_EVENTS
from ..harness import HARNESSES, descriptor
from ..mcp_contract import claude_read_permissions
from .base import BackendAdapter, SpawnOptions, SpawnSpec

log = logging.getLogger(__name__)


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
    return descriptor("claude").data_home()


def _write_config_if_changed(path: Path, payload: object) -> Path:
    """Write a generated config file atomically, and only when it differs.

    These files carry the same bytes on every daemon start, and both are listed
    in the Agent Config tab as configuration sources. Replacing an identical
    file still moves its mtime, which is what made every session that predated a
    daemon restart report its MCP registration and hook settings as having
    changed. Skipping the no-op write keeps the file, and the claim, stable.
    """
    body = json.dumps(payload, indent=2)
    try:
        if path.read_text(encoding="utf-8") == body:
            return path
    except (OSError, UnicodeError):
        pass
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)
    return path


def _bash_executable_path(executable: str) -> str:
    """Translate a Windows executable path for Claude's Bash hook runner."""
    normalized = executable.replace("\\", "/")
    if len(normalized) >= 3 and normalized[1:3] == ":/":
        return f"/{normalized[0].lower()}{normalized[2:]}"
    return normalized


def _hook_command(event: str, executable: str | None = None, identity: Path | None = None) -> str:
    python = _bash_executable_path(executable or sys.executable)
    argv = [python, "-m", "swe_mux.hook_client", event]
    if identity is not None:
        # Native path, deliberately not translated for the Bash hook runner:
        # `shlex.join` single-quotes it, POSIX single quotes keep the backslashes
        # literal, and the reader is Python on Windows — which cannot open the
        # `/c/...` form the interpreter path needs.
        argv.extend(["--identity", str(identity)])
    return shlex.join(argv)


class ClaudeAdapter(BackendAdapter):
    name = "claude"
    # Spawned with an injected `--session-id` and per-session hook settings, so
    # every conversation replacement (`/clear`, in-CLI `/resume`) is reported by
    # the CLI itself via the SessionStart ingress. A conversation parked into a
    # background job is reported by the CLI too, through `parkedJobId` in its
    # per-process state file, because no hook can speak for it (`cli_state.py`).
    # Both are the CLI's own report; the transcript-switch heuristic must never
    # move a Claude session.
    reports_conversation_rollover = descriptor(name).reports_conversation_rollover
    # mux spawns with `--session-id <mux id>`, so the CLI writes exactly that
    # conversation and native_session_id is authoritative from the first moment.
    assigns_conversation_id = descriptor(name).assigns_conversation_id
    # Transcripts live under `projects/<encoded cwd>/`, so a cwd change *moves the
    # existing file*. Entering a Claude native worktree is exactly that, and it is
    # why a live session's transcript path is never permanently true.
    resolves_transcript_by_cwd = descriptor(name).resolves_transcript_by_cwd

    def __init__(
        self,
        default_exe: str = "claude.exe",
        data_dir: Path | None = None,
        default_args: list[str] | None = None,
        mcp_url: str | None = None,
        *,
        name: str = "claude",
        config_dir_name: str = ".claude",
        script_base_name: str = "claude",
        data_home_resolver: Callable[[], Path] | None = None,
        user_home_resolver: Callable[[], Path] | None = None,
        instrument: bool = True,
        approval_hook_timeout: float = 5.0,
    ) -> None:
        self.name = name
        self.shim_name = f"{name}.cmd"
        self.config_dir_name = config_dir_name
        self.script_base_name = script_base_name
        self._data_home_resolver = data_home_resolver or (
            claude_data_home if name == "claude" else lambda: Path.home() / config_dir_name
        )
        self._user_home_resolver = user_home_resolver or Path.home
        family = descriptor(name) if name in HARNESSES else descriptor("claude")
        self.reports_conversation_rollover = family.reports_conversation_rollover
        self.assigns_conversation_id = family.assigns_conversation_id
        self.resolves_transcript_by_cwd = family.resolves_transcript_by_cwd
        self.default_exe = default_exe
        self.default_args = default_args or []
        self.data_dir = data_dir
        # "Launch clean": when instrumentation is off, no hook settings are written
        # or passed, so this harness runs unobserved (no status/history/queue).
        self.instrument = instrument
        self._mux_read_permissions = claude_read_permissions() if mcp_url else []
        self._approval_hook_timeout = max(1, int(round(approval_hook_timeout)))
        if data_dir:
            data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = (
            self._write_hook_settings(data_dir) if data_dir and instrument else None
        )
        self.mcp_config_path = (
            self._write_mcp_config(data_dir, mcp_url) if data_dir and mcp_url else None
        )

    def _write_mcp_config(self, data_dir: Path, mcp_url: str) -> Path:
        """One static registration file for the mux MCP server (`--mcp-config`).

        The URL is literal (stable daemon port); only the bearer token is
        per-session, carried by `${MUX_MCP_TOKEN}` env expansion so no
        per-session file is needed. `--mcp-config` *adds* servers, so a user's
        own MCP configuration is untouched.
        """
        path = data_dir / f"{self.script_base_name}-mcp.json"
        payload = {
            "mcpServers": {
                "mux": {
                    "type": "http",
                    "url": mcp_url,
                    "headers": {"Authorization": "Bearer ${MUX_MCP_TOKEN}"},
                }
            }
        }
        return _write_config_if_changed(path, payload)

    def _write_hook_settings(self, data_dir: Path, identity: Path | None = None) -> Path:
        path = data_dir / f"{self.script_base_name}-hooks.json"
        hooks: dict[str, list[dict[str, object]]] = {}
        family = descriptor(self.name) if self.name in HARNESSES else descriptor("claude")
        for event in family.hook_events:
            command = _hook_command(event, identity=identity)
            entry: dict[str, object] = {"type": "command", "command": command}
            if family.hook_approval_decisions and event in DECISION_HOOK_EVENTS:
                # The CLI's own default is 600 s, and a timed-out hook does not
                # block — it falls through to the normal permission prompt. So
                # this is not a correctness gate; it is the difference between a
                # daemon that is wedged costing one prompt a few seconds and
                # costing ten minutes of an agent parked on a dialog.
                entry["timeout"] = self._approval_hook_timeout
            hooks[event] = [{"hooks": [entry]}]
        payload: dict[str, object] = {"hooks": hooks}
        if self._mux_read_permissions:
            payload["permissions"] = {"allow": self._mux_read_permissions}
        return _write_config_if_changed(path, payload)

    @staticmethod
    def _write_hook_identity(directory: Path, opts: SpawnOptions) -> Path | None:
        """Materialize this pane's hook credentials beside its settings file.

        On disk rather than in the environment because the environment does not
        survive Claude's background-job hand-off: a parked conversation is run by
        a shared `claude daemon run` process whose environment belongs to
        whichever CLI first started it, while `--settings` is passed per request
        and always names the requesting pane. Rewritten on every spawn so the
        file can never hold a superseded secret.
        """
        if not opts.hook_url or not opts.hook_secret:
            return None
        path = directory / "hook-identity.json"
        payload = {"url": opts.hook_url, "secret": opts.hook_secret}
        if opts.hook_spool:
            payload["spool"] = opts.hook_spool
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)
        path.chmod(0o600)
        return path

    def _session_settings_dir(self, session_id: str | None) -> Path | None:
        if not self.data_dir or not session_id:
            return None
        return self.data_dir / "sessions" / session_id

    def _session_settings(self, opts: SpawnOptions) -> Path | None:
        """Write this pane's settings and hook identity; return the settings path."""
        if not self.instrument:
            return None
        directory = self._session_settings_dir(opts.session_id)
        if directory is None:
            return self.settings_path
        directory.mkdir(parents=True, exist_ok=True)
        return self._write_hook_settings(directory, self._write_hook_identity(directory, opts))

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
        settings = self._session_settings(opts)
        if settings:
            args.extend(["--settings", str(settings)])
        worktree_args = (
            self.worktree_spawn_args(opts.worktree_project_root)
            if opts.worktree_project_root is not None
            else ()
        )
        return [*args, *worktree_args, *self.default_args, *opts.args]

    @staticmethod
    def _canonical_project_key(path: Path) -> str:
        return path.resolve().as_posix()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def _preseed_trust(self, project_root: Path, worktree_path: Path) -> None:
        path = self._user_home_resolver() / ".claude.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OSError(f"Claude trust file is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Claude trust file root must be an object")
        projects = payload.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("Claude trust projects must be an object")
        target_key = self._canonical_project_key(worktree_path)
        if target_key in projects:
            return
        source = projects.get(self._canonical_project_key(project_root), {})
        entry = copy.deepcopy(source) if isinstance(source, dict) else {}
        entry["hasTrustDialogAccepted"] = True
        projects[target_key] = entry
        self._atomic_write(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))

    def _copy_local_permissions(self, project_root: Path, worktree_path: Path) -> None:
        source = project_root / ".claude" / "settings.local.json"
        if not source.is_file():
            return
        source_payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(source_payload, dict):
            raise ValueError("primary Claude local settings must be an object")
        target = worktree_path / ".claude" / "settings.local.json"
        if not target.exists():
            self._atomic_write(
                target, (json.dumps(source_payload, indent=2) + "\n").encode("utf-8")
            )
            return
        target_payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(target_payload, dict):
            raise ValueError("worktree Claude local settings must be an object")
        source_permissions = source_payload.get("permissions")
        if isinstance(source_permissions, dict) and isinstance(
            source_permissions.get("allow"), list
        ):
            permissions = target_payload.setdefault("permissions", {})
            if not isinstance(permissions, dict):
                raise ValueError("worktree Claude permissions must be an object")
            current = permissions.get("allow", [])
            if not isinstance(current, list):
                raise ValueError("worktree Claude permission allowlist must be an array")
            permissions["allow"] = list(dict.fromkeys([*current, *source_permissions["allow"]]))
        self._atomic_write(target, (json.dumps(target_payload, indent=2) + "\n").encode("utf-8"))

    def preflight_worktree(self, project_root: Path, worktree_path: Path) -> None:
        failures: list[str] = []
        operations: tuple[tuple[str, Callable[[], None]], ...] = (
            ("trust", lambda: self._preseed_trust(project_root, worktree_path)),
            ("permissions", lambda: self._copy_local_permissions(project_root, worktree_path)),
        )
        for label, operation in operations:
            try:
                operation()
            except Exception as exc:  # noqa: BLE001 - trust preflight is explicitly best effort
                failures.append(f"{label}: {exc}")
                log.warning(
                    "claude_worktree_preflight_failed phase=%s project_root=%s "
                    "worktree=%s error_type=%s",
                    label,
                    project_root,
                    worktree_path,
                    type(exc).__name__,
                )
        if failures:
            raise RuntimeError("; ".join(failures))

    def worktree_spawn_args(self, project_root: Path) -> tuple[str, ...]:
        return ("--add-dir", str(project_root.resolve()))

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
        return self._data_home_resolver() / "projects" / encode_cwd(cwd) / f"{native_id}.jsonl"

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
        root = self._data_home_resolver() / "projects"
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

    def pending_transcript_path(self, session_id: str, current: Path) -> None:
        del session_id, current
        return None

    def graceful_exit_keys(self) -> str:
        return "/exit\r"

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        root = self._data_home_resolver() / "projects" / encode_cwd(cwd)
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

    def model_context_window(self, provider: str, model: str) -> int:
        del provider, model
        return 0

    def cleanup(self, session_id: str) -> None:
        if self.data_dir:
            shutil.rmtree(self.data_dir / "sessions" / session_id, ignore_errors=True)

    def session_env(self, session_id: str) -> dict[str, str]:
        # Path only, with a bare file materialized when none exists yet. The
        # pane's own spawn already wrote the identity-bearing settings file and
        # rewriting it here would strip `--identity` back out of it; a pane of
        # another backend has no such file, and the bare one it gets is what any
        # nested Claude it launches itself runs with.
        directory = self._session_settings_dir(session_id)
        settings = self.settings_path
        if directory is not None:
            settings = directory / f"{self.script_base_name}-hooks.json"
            if not settings.exists():
                directory.mkdir(parents=True, exist_ok=True)
                self._write_hook_settings(directory)
        key = f"MUX_{self.name.upper().replace('-', '_')}_SETTINGS"
        return {key: str(settings)} if settings else {}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        self.default_args = list(args)

    def media_reference(self, path: Path) -> str:
        return str(path)
