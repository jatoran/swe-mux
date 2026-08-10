from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..harness import descriptor
from .base import BackendAdapter, SpawnOptions, SpawnSpec

_TITLE_SLOT_BYTES = 256
_SESSION_ID_FROM_NAME = re.compile(r"_(?P<id>[^_]+)\.jsonl$")
def omp_hook_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "omp_mux_hook.ts"


def materialize_mux_extension(
    root: Path,
    *,
    mcp_url: str | None = None,
    mcp_token: str | None = None,
) -> Path:
    """Build the isolated OMP extension package used by one mux session.

    OMP discovers sibling ``.mcp.json`` files only when ``--extension`` names a
    directory, and that loader does not expand environment variables.  A
    session-private package therefore carries the hook plus its own literal
    bearer token without changing the project or the user's OMP configuration.
    """
    root.mkdir(parents=True, exist_ok=True)
    hook = root / "index.ts"
    temporary_hook = root / "index.ts.tmp"
    temporary_hook.write_bytes(omp_hook_path().read_bytes())
    temporary_hook.replace(hook)
    if mcp_url and mcp_token:
        payload = {
            "mcpServers": {
                "mux": {
                    "type": "http",
                    "url": mcp_url,
                    "headers": {"Authorization": f"Bearer {mcp_token}"},
                }
            }
        }
        config = root / ".mcp.json"
        temporary_config = root / ".mcp.json.tmp"
        temporary_config.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary_config.replace(config)
        try:
            config.chmod(0o600)
        except OSError:
            pass
    elif (root / ".mcp.json").exists():
        stale = root / ".trash"
        stale.mkdir(exist_ok=True)
        (root / ".mcp.json").replace(stale / ".mcp.json")
    return root


def retire_mux_extension(root: Path, session_id: str) -> None:
    source = root / session_id
    if not source.exists():
        return
    trash = root.parent / ".trash" / "omp-extensions"
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / session_id
    suffix = 1
    while destination.exists():
        destination = trash / f"{session_id}-{suffix}"
        suffix += 1
    shutil.move(str(source), str(destination))


def with_mux_hook(args: list[str], extension_path: Path | None = None) -> list[str]:
    target = str(extension_path or omp_hook_path())
    if target in args or any(arg == f"--extension={target}" for arg in args):
        return args
    return ["--extension", target, *args]


def session_header(path: Path) -> dict[str, Any] | None:
    """Read the ``{"type": "session"}`` header from a pi-family session file.

    Shared by oh-my-pi and upstream pi because the two forks write the same
    header record. Current omp files begin with a fixed 256-byte title slot;
    legacy omp files and every pi file begin with the header itself, so this
    inspects logical records first and only then probes the byte boundary. That
    boundary probe exists for a torn title-slot rewrite, where the slot's
    trailing newline is briefly missing.
    """
    try:
        with path.open("rb") as handle:
            prefix = handle.read(64 * 1024)
    except OSError:
        return None
    for raw in prefix.splitlines()[:3]:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("type") == "session":
            return value
    if len(prefix) > _TITLE_SLOT_BYTES:
        raw = prefix[_TITLE_SLOT_BYTES:].splitlines()[0]
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if isinstance(value, dict) and value.get("type") == "session":
            return value
    return None


@dataclass(frozen=True, slots=True)
class TerminalBreadcrumb:
    cwd: Path
    session_file: Path
    fresh: bool


class OmpAdapter(BackendAdapter):
    name = "omp"
    reports_conversation_rollover = True
    assigns_conversation_id = False
    resolves_transcript_by_cwd = True

    def __init__(
        self,
        default_exe: str = "omp",
        default_args: list[str] | None = None,
        *,
        data_home: Path | None = None,
        data_dir: Path | None = None,
        mcp_url: str | None = None,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self._data_home = data_home
        self._extension_root = data_dir / "omp-extensions" if data_dir else None
        self._mcp_url = mcp_url
        self._model_context_windows: dict[tuple[str, str], int] | None = None

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        extension = self._session_extension(opts.session_id or sid, opts.mcp_token)
        return SpawnSpec(
            opts.exe or self.default_exe,
            tuple(with_mux_hook([*self.default_args, *opts.args], extension)),
            self._omp_env(),
        )

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        extension = self._session_extension(opts.session_id or native_id, opts.mcp_token)
        return SpawnSpec(
            opts.exe or self.default_exe,
            tuple(
                with_mux_hook(
                    ["--resume", native_id, *self.default_args, *opts.args], extension
                )
            ),
            self._omp_env(),
        )

    def _session_extension(self, session_id: str, token: str | None) -> Path | None:
        if self._extension_root is None:
            return None
        return materialize_mux_extension(
            self._extension_root / session_id,
            mcp_url=self._mcp_url,
            mcp_token=token,
        )

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        del recorded_cwd, target_cwd
        return True

    @property
    def data_home(self) -> Path:
        return self._data_home or descriptor("omp").data_home()

    @staticmethod
    def _term_session_value(session_id: str) -> str:
        return f"swe-mux-{session_id}"

    @staticmethod
    def _omp_env() -> dict[str, str]:
        """OMP-specific spawn tuning, layered over the shared session terminal env.

        Colour capability, marker shadowing, and colour forcing are applied to
        every session centrally (``spawn_contract.session_terminal_env``), and
        OMP's per-pane ``TERM_SESSION_ID`` comes from :meth:`session_env`. What
        remains here are two ``PI_*`` knobs. Synchronized paints (DEC 2026) are
        declined because a bounded replay window can end on the open half of a
        paint bracket, which a fresh client then withholds until the live stream
        closes it; ``PI_FORCE_IMAGE_PROTOCOL=off`` keeps OMP's text fallback for
        attached files, as this frontend implements no image protocol. (Not the
        2026-08-06 black-pane diagnosis — that was xterm's DECRQM handler crashing,
        fixed in frontend/scripts/patch-xterm-requestmode.mjs.)
        """
        return {
            "PI_NO_SYNC_OUTPUT": "1",
            "PI_FORCE_IMAGE_PROTOCOL": "off",
        }

    @classmethod
    def _terminal_id(cls, session_id: str) -> str:
        # getTerminalId() prefixes TERM_SESSION_ID with "apple-" on every OS.
        return f"apple-{cls._term_session_value(session_id)}"

    def _breadcrumb_path(self, session_id: str) -> Path:
        return self.data_home / "terminal-sessions" / self._terminal_id(session_id)

    def _read_breadcrumb(self, session_id: str) -> TerminalBreadcrumb | None:
        try:
            lines = self._breadcrumb_path(session_id).read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
            return None
        return TerminalBreadcrumb(
            cwd=Path(lines[0]),
            session_file=Path(lines[1]),
            fresh=len(lines) >= 3 and lines[2].strip() == "fresh",
        )

    @staticmethod
    def _canonical(path: Path) -> Path:
        try:
            return path.resolve(strict=False)
        except OSError:
            return Path(os.path.abspath(path))

    @classmethod
    def _same_path(cls, left: Path | str, right: Path | str) -> bool:
        return os.path.normcase(str(cls._canonical(Path(left)))) == os.path.normcase(
            str(cls._canonical(Path(right)))
        )

    @classmethod
    def _session_dir_names(cls, cwd: Path) -> tuple[str, ...]:
        """Current 17.2.10 bucket plus the 17.2.5-17.2.8 hashed bucket."""
        canonical = cls._canonical(cwd)
        home = cls._canonical(Path.home())
        temp = cls._canonical(Path(tempfile.gettempdir()))

        def relative_to(root: Path) -> Path | None:
            try:
                return canonical.relative_to(root)
            except ValueError:
                return None

        home_relative = relative_to(home)
        temp_relative = relative_to(temp)
        if home_relative is not None:
            scope = "home"
            encoded = "-" + re.sub(r"[/\\:]", "-", str(home_relative))
        elif temp_relative is not None:
            scope = "tmp"
            suffix = re.sub(r"[/\\:]", "-", str(temp_relative))
            encoded = "-tmp" + (f"-{suffix}" if suffix else "")
        else:
            scope = "abs"
            absolute = re.sub(r"[/\\:]", "-", str(canonical).lstrip("/\\"))
            encoded = f"--{absolute}--"

        normalized = str(canonical).replace("\\", "/")
        readable = re.sub(r"[^a-zA-Z0-9._-]+", "-", canonical.name).strip("-")[-80:]
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        hashed = f"{scope}-{readable or 'project'}-{digest}"
        return tuple(dict.fromkeys((encoded, hashed)))

    def _candidate_dirs(self, cwd: Path) -> tuple[Path, ...]:
        root = self.data_home / "sessions"
        return tuple(root / name for name in self._session_dir_names(cwd))

    @staticmethod
    def _header(path: Path) -> dict[str, Any] | None:
        return session_header(path)

    def _session_files(self) -> list[Path]:
        root = self.data_home / "sessions"
        try:
            return list(root.glob("*/*.jsonl"))
        except OSError:
            return []

    def _resolve_previous_path(self, previous: Path) -> Path | None:
        matches: list[tuple[float, int, Path]] = []
        for candidate in self._session_files():
            header = self._header(candidate)
            prior = header.get("previousSessionFiles") if header else None
            if not isinstance(prior, list) or not any(
                isinstance(item, str) and self._same_path(item, previous) for item in prior
            ):
                continue
            try:
                stat = candidate.stat()
            except OSError:
                continue
            matches.append((stat.st_mtime, stat.st_size, candidate))
        return max(matches)[2] if matches else None

    def _breadcrumb_transcript(self, session_id: str) -> Path | None:
        breadcrumb = self._read_breadcrumb(session_id)
        if breadcrumb is None:
            return None
        path = breadcrumb.session_file
        if path.is_file() or breadcrumb.fresh:
            return path
        return self._resolve_previous_path(path)

    def transcript_path(self, native_id: str, cwd: Path) -> Path | None:
        breadcrumb = self._breadcrumb_transcript(native_id)
        if breadcrumb is not None:
            return breadcrumb
        matches: list[tuple[float, int, Path]] = []
        for directory in self._candidate_dirs(cwd):
            for candidate in directory.glob("*.jsonl") if directory.is_dir() else ():
                if self.transcript_native_id(candidate) != native_id:
                    continue
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                matches.append((stat.st_mtime, stat.st_size, candidate))
        if matches:
            return max(matches)[2]
        return self.locate_transcript(native_id)

    def locate_transcript(self, native_id: str) -> Path | None:
        if not native_id:
            return None
        breadcrumb = self._breadcrumb_transcript(native_id)
        if breadcrumb is not None:
            return breadcrumb
        matches: list[tuple[float, int, Path]] = []
        for candidate in self._session_files():
            if self.transcript_native_id(candidate) != native_id:
                continue
            try:
                stat = candidate.stat()
            except OSError:
                continue
            matches.append((stat.st_mtime, stat.st_size, candidate))
        return max(matches)[2] if matches else None

    def pending_transcript_path(self, session_id: str, current: Path) -> Path | None:
        breadcrumb = self._read_breadcrumb(session_id)
        if (
            breadcrumb is None
            or not breadcrumb.fresh
            or breadcrumb.session_file.exists()
            or self._same_path(breadcrumb.session_file, current)
        ):
            return None
        return breadcrumb.session_file

    def graceful_exit_keys(self) -> str:
        # Ctrl+C clears an in-progress draft and Ctrl+D exits from an empty editor.
        return "\x03\x04"

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        recent: list[tuple[float, Path, str]] = []
        seen: set[str] = set()
        for directory in self._candidate_dirs(cwd):
            for path in directory.glob("*.jsonl") if directory.is_dir() else ():
                key = os.path.normcase(str(path))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                if modified + 2 < created_at:
                    continue
                header = self._header(path)
                native_id = str(header.get("id") or "") if header else ""
                header_cwd = header.get("cwd") if header else None
                if not native_id or not isinstance(header_cwd, str):
                    continue
                if self._same_path(header_cwd, cwd):
                    recent.append((modified, path, native_id))
        return recent

    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None:
        while not stop.is_set():
            breadcrumb = self._breadcrumb_transcript(native_id)
            if breadcrumb is not None:
                return breadcrumb
            exact = [
                item for item in self.recent_transcripts(cwd, created_at) if item[2] == native_id
            ]
            if exact:
                return max(exact)[1]
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except TimeoutError:
                pass
        return None

    def transcript_native_id(self, path: Path) -> str | None:
        header = self._header(path)
        if header is not None:
            value = header.get("id")
            return value if isinstance(value, str) and value else None
        # A fresh breadcrumb announces the path before the file exists. omp's
        # filename suffix is the session id, so the pending boundary is still
        # strongly identified.
        match = _SESSION_ID_FROM_NAME.search(path.name)
        return match.group("id") if match else None

    def model_context_window(self, provider: str, model: str) -> int:
        """Read OMP's own cached model metadata without invoking or mutating OMP."""
        if self._model_context_windows is None:
            self._model_context_windows = self._load_model_context_windows()
        return self._model_context_windows.get((provider, model), 0)

    def _load_model_context_windows(self) -> dict[tuple[str, str], int]:
        path = self.data_home / "models.db"
        result: dict[tuple[str, str], int] = {}
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT provider_id,models FROM model_cache").fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            rows = []
        for provider_id, raw_models in rows:
            try:
                models = json.loads(raw_models)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(models, list):
                continue
            for item in models:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("id")
                window = item.get("contextWindow")
                if (
                    isinstance(provider_id, str)
                    and isinstance(model_id, str)
                    and isinstance(window, int)
                    and window > 0
                ):
                    result[(provider_id, model_id)] = window
        # The catalog is optional and can be absent before OMP's first model
        # refresh. Preserve measured support for the bundled model used by the
        # integration probe without guessing for unknown custom models.
        result.setdefault(("anthropic", "claude-opus-4-8"), 1_000_000)
        return result

    def cleanup(self, session_id: str) -> None:
        if self._extension_root is None:
            return
        retire_mux_extension(self._extension_root, session_id)

    def session_env(self, session_id: str) -> dict[str, str]:
        return {"TERM_SESSION_ID": self._term_session_value(session_id)}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        self.default_args = list(args)

    def media_reference(self, path: Path) -> str:
        return str(path)
