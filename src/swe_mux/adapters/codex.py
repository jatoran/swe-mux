from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
import uuid
from collections.abc import Callable
from pathlib import Path

from ..codex_tui import with_scrollback_safe_tui
from ..harness import descriptor
from .base import BackendAdapter, SpawnOptions, SpawnSpec

# Shared across every live Codex session: they all scan the same tree on the same
# cadence, so one pass per window instead of one per session per tick.
_ROLLOUT_CACHE_SECONDS = 2.0
_ROLLOUT_CACHE: dict[str, tuple[float, list[tuple[float, Path]]]] = {}

CODEX_LIFECYCLE_HOOK_EVENTS = descriptor("codex").hook_events


def codex_data_home() -> Path:
    return descriptor("codex").data_home()


def _codex_hook_commands(event: str, executable: str | None = None) -> tuple[str, str]:
    argv = [executable or sys.executable, "-m", "swe_mux.hook_client", event]
    return shlex.join(argv), subprocess.list2cmdline(argv)


def codex_lifecycle_hook_args(existing_args: list[str] | None = None) -> list[str]:
    """Inline additive Codex lifecycle hooks without rewriting user configuration.

    The command is session-independent: its exact definition hashes the same in every
    pane, so Codex's non-managed hook trust review is needed once rather than once per
    mux session. Per-session authentication remains in the inherited MUX_HOOK_* env.
    """
    existing = existing_args or []
    configured = "\n".join(existing)
    args: list[str] = []
    for event in CODEX_LIFECYCLE_HOOK_EVENTS:
        if f"hooks.{event}" in configured:
            continue
        command, windows_command = _codex_hook_commands(event)
        timeout = 3 if event == "SessionEnd" else 15
        value = (
            f'hooks.{event}=[{{ hooks = [{{ type = "command", '
            f"command = {json.dumps(command)}, "
            f"command_windows = {json.dumps(windows_command)}, "
            f"timeout = {timeout} }}] }}]"
        )
        args.extend(["-c", value])
    return args


class CodexAdapter(BackendAdapter):
    name = "codex"
    # Lifecycle hooks are additive but user/admin policy can disable or leave them
    # untrusted, so `/new` retains the transcript-switch fallback. A resume reports
    # the id it already had, so it is not a rollover and nothing here fires for it.
    reports_conversation_rollover = descriptor(name).reports_conversation_rollover
    # Codex mints its own thread id, so a fresh session carries the mux session id
    # as a placeholder until SessionStart reports the real id. Transcript discovery
    # can then exact-match it; elimination remains the hookless fallback.
    assigns_conversation_id = descriptor(name).assigns_conversation_id
    # Rollouts live in a date tree addressed by thread id, so a Codex conversation's
    # file never moves when the pane's working directory does.
    resolves_transcript_by_cwd = descriptor(name).resolves_transcript_by_cwd

    def __init__(
        self,
        default_exe: str = "codex.exe",
        notify: bool = False,
        default_args: list[str] | None = None,
        command_resolver: Callable[[str], tuple[str, tuple[str, ...]]] | None = None,
        mcp_url: str | None = None,
        *,
        name: str = "codex",
        data_home_resolver: Callable[[], Path] | None = None,
        rollout_file_prefix: str = "rollout-",
    ) -> None:
        self.name = name
        self._data_home_resolver = data_home_resolver or descriptor(name).data_home
        self.rollout_file_prefix = rollout_file_prefix
        family = descriptor(name)
        self.reports_conversation_rollover = family.reports_conversation_rollover
        self.assigns_conversation_id = family.assigns_conversation_id
        self.resolves_transcript_by_cwd = family.resolves_transcript_by_cwd
        self.default_exe = default_exe
        self.default_args = default_args or []
        self.command_resolver = command_resolver
        self.notify_program = (
            [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"] if notify else None
        )
        self.mcp_url = mcp_url

    @staticmethod
    def _canonical_project_key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    @staticmethod
    def _table_range(text: str, project_key: str) -> tuple[int, int] | None:
        headers = list(re.finditer(r"(?m)^[ \t]*(\[[^\n]+\])[ \t]*$", text))
        for index, match in enumerate(headers):
            try:
                parsed = tomllib.loads(f"{match.group(1)}\nprobe = true\n")
            except tomllib.TOMLDecodeError:
                continue
            projects = parsed.get("projects")
            if isinstance(projects, dict) and project_key in projects:
                end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
                return match.end(), end
        return None

    def preflight_worktree(self, project_root: Path, worktree_path: Path) -> None:
        del project_root
        config = self._data_home_resolver() / "config.toml"
        text = config.read_text(encoding="utf-8") if config.exists() else ""
        parsed = tomllib.loads(text) if text.strip() else {}
        key = self._canonical_project_key(worktree_path)
        projects = parsed.get("projects", {})
        if isinstance(projects, dict):
            existing = projects.get(key)
            if isinstance(existing, dict) and existing.get("trust_level") == "trusted":
                return
        section = self._table_range(text, key)
        if section is None:
            separator = (
                ""
                if not text or text.endswith("\n\n")
                else ("\n" if text.endswith("\n") else "\n\n")
            )
            text += f'{separator}[projects.{json.dumps(key)}]\ntrust_level = "trusted"\n'
        else:
            start, end = section
            body = text[start:end]
            trust = re.search(r"(?m)^[ \t]*trust_level[ \t]*=[ \t]*[^\n]+$", body)
            if trust:
                body = body[: trust.start()] + 'trust_level = "trusted"' + body[trust.end() :]
            else:
                body = '\ntrust_level = "trusted"' + body
            text = text[:start] + body + text[end:]
        tomllib.loads(text)
        self._atomic_write(config, text.encode("utf-8"))

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

    def worktree_spawn_args(self, project_root: Path) -> tuple[str, ...]:
        root = json.dumps(str(project_root.resolve()))
        return ("-c", f"sandbox_workspace_write.writable_roots=[{root}]")

    def _args(self, args: list[str], worktree_project_root: Path | None = None) -> list[str]:
        configured = with_scrollback_safe_tui([*self.default_args, *args])
        prefix = [
            *(
                self.worktree_spawn_args(worktree_project_root)
                if worktree_project_root is not None
                else ()
            ),
            *self._mcp_args(),
        ]
        if self.notify_program:
            prefix = [
                "-c",
                f"notify={json.dumps(self.notify_program)}",
                *codex_lifecycle_hook_args(configured),
                *prefix,
            ]
        return [*prefix, *configured]

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        del sid
        executable = opts.exe or self.default_exe
        resolved, prefix = (
            self.command_resolver(executable) if self.command_resolver else (executable, ())
        )
        return SpawnSpec(
            resolved,
            (*prefix, *self._args(opts.args, opts.worktree_project_root)),
        )

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        executable = opts.exe or self.default_exe
        resolved, prefix = (
            self.command_resolver(executable) if self.command_resolver else (executable, ())
        )
        return SpawnSpec(
            resolved,
            (
                *prefix,
                *self._args(
                    ["resume", native_id, *opts.args],
                    opts.worktree_project_root,
                ),
            ),
        )

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        """`codex resume` always continues the conversation, wherever the pane runs.

        Codex addresses a conversation by thread id and appends to the rollout that
        id already owns, so a resumed pane is the same conversation and inherits its
        history row. Measured 2026-08-06 on codex-cli 0.146.1: one 5.2 MB rollout
        created 08-03 18:54 holds 2,900 records written across four separate panes,
        the original plus three later resumes, and no new rollout exists for any of
        them. The cwd is irrelevant here — unlike Claude, Codex does not resolve
        transcripts through it.

        This answers for the resume flow, which only ever resumes a conversation no
        live pane holds (`409 conversation_live` otherwise). Resuming a *live* one is
        Branch's trick for making the CLI fork a child thread
        (``parent_thread_id``), and that pane keeps its own row. Either way the
        inheritance is self-correcting: a pane that reports a conversation id other
        than the one it resumed goes through the ordinary rollover path, which retires
        the inherited row and mints a run of its own.
        """
        del recorded_cwd, target_cwd
        return True

    def transcript_path(self, native_id: str, cwd: Path) -> Path | None:
        """The rollout this thread id owns, located by name rather than by mtime.

        `codex resume` appends to the rollout the conversation already owns, so a
        resumed pane's transcript is a file that existed before the pane did. The
        recency window in ``recent_transcripts`` therefore cannot see it, and waiting
        for the mtime to catch up is not a fallback: Windows leaves an open file's
        mtime frozen at creation while the file grows, so a live rollout can report a
        timestamp hours older than its newest record (observed at 5 MB and 71 minutes
        of divergence). A conversation whose id is *known* needs no window — the
        rollout file name carries the id, and the file's own first record then proves
        the identity, which is the evidence binding requires.

        ``None`` when no rollout answers to the id. That covers the placeholder mux
        id a fresh Codex pane carries until discovery names its real conversation,
        and a child rollout (``parent_thread_id``), which is never a root
        conversation — both are cases where "where does this conversation live" has
        no truthful answer yet.
        """
        del cwd  # Rollouts live in a date tree, never under the session's cwd.
        root = self._data_home_resolver() / "sessions"
        if not native_id or not root.exists():
            return None
        suffix = f"-{native_id}.jsonl".casefold()
        for _modified, path in self._rollouts(root):
            if not path.name.casefold().endswith(suffix):
                continue
            if self.transcript_native_id(path) == native_id:
                return path
        return None

    def locate_transcript(self, native_id: str) -> Path | None:
        """Identical to ``transcript_path`` here: the lookup never used the cwd.

        Kept as its own method so callers do not have to know which backends ignore
        the cwd argument, and so a Codex session recovers through the same path a
        relocated Claude one does.
        """
        return self.transcript_path(native_id, Path())

    def pending_transcript_path(self, session_id: str, current: Path) -> None:
        del session_id, current
        return None

    def graceful_exit_keys(self) -> str:
        return "/exit\r"

    def _association(self, path: Path) -> tuple[str, str] | None:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                first = json.loads(handle.readline())
            payload = first.get("payload") or {}
            if payload.get("parent_thread_id"):
                return None
            if payload.get("id") and payload.get("cwd"):
                return str(payload["id"]), str(Path(payload["cwd"]).resolve())
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _rollouts(self, root: Path) -> list[tuple[float, Path]]:
        """Every rollout in the tree, newest mtime first, cached briefly and shared.

        Codex stores rollouts in a flat date tree that mux never prunes, and this
        runs on a 2-second switch-watch tick *per live Codex session*. Re-globbing
        and re-stat-ing thousands of files per tick, forever, was tens of
        thousands of stat calls a second on an install with real history — pure
        waste whenever no switch is happening. The window is short enough that a
        genuine in-CLI resume is still noticed within one extra tick.

        The whole listing is cached rather than only the newest few, because
        ``transcript_path`` looks up a *known* conversation by file name and must not
        pay a second walk for it: a resumed rollout is old by mtime and would never
        appear in a newest-first slice.

        **``os.scandir``, not ``Path.glob`` + ``path.stat()``.** Windows fills a
        ``DirEntry``'s stat fields from the directory enumeration itself, so the mtime
        this needs is already in hand; ``path.stat()`` spends a fresh syscall per file
        to fetch what the walk just read. Measured 2026-08-05 against a real tree of
        1,314 rollouts in 158 directories: 35.8 ms to 7.5 ms, **4.8x**, byte-identical
        results. It is also not free to be slow here — this runs synchronously on the
        event loop, so the old cost was a ~36 ms daemon-wide stall every couple of
        seconds rather than merely wasted CPU.

        The date tree cannot be pruned by directory name to go faster: ``codex resume``
        appends to the original rollout, so a file under an old date can have the
        newest mtime in the tree, and that is exactly the case this has to catch.
        """
        now = time.monotonic()
        key = str(root)
        cached = _ROLLOUT_CACHE.get(key)
        if cached and now - cached[0] < _ROLLOUT_CACHE_SECONDS:
            return cached[1]
        found: list[tuple[float, Path]] = []
        stack = [str(root)]
        while stack:
            try:
                with os.scandir(stack.pop()) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.name.startswith(
                                self.rollout_file_prefix
                            ) and entry.name.endswith(".jsonl"):
                                found.append(
                                    (entry.stat(follow_symlinks=False).st_mtime, Path(entry.path))
                                )
                        except OSError:
                            continue
            except OSError:
                continue
        found.sort(key=lambda item: item[0], reverse=True)
        _ROLLOUT_CACHE[key] = (now, found)
        return found

    def _newest_rollouts(self, root: Path) -> list[tuple[float, Path]]:
        """The 20 newest rollouts: the bound on cwd/time correlation for a new pane."""
        return self._rollouts(root)[:20]

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        root = self._data_home_resolver() / "sessions"
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

    def model_context_window(self, provider: str, model: str) -> int:
        del provider, model
        return 0

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
