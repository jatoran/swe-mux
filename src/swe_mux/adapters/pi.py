from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..harness import descriptor
from ..shim_paths import which_real
from .base import BackendAdapter, SpawnOptions, SpawnSpec
from .omp import session_header

_SESSION_ID_FROM_NAME = re.compile(r"_(?P<id>[^_]+)\.jsonl$")

# `models.generated.js` in `@earendil-works/pi-ai` is emitted by
# `scripts/generate-models.ts` with a fixed key order, so each model object runs
# `id` -> `provider` -> ... -> `contextWindow`. Matching across that span is
# stable against new fields being added between them and yields nothing rather
# than a wrong number if the generator ever reorders.
_MODEL_ENTRY = re.compile(
    r'id:\s*"(?P<model>[^"]+)".*?provider:\s*"(?P<provider>[^"]+)".*?contextWindow:\s*(?P<window>\d+)',
    re.DOTALL,
)
_MODEL_CATALOG_RELATIVE = Path("@earendil-works/pi-ai/dist/models.generated.js")


def pi_hook_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "pi_mux_hook.ts"


def pi_session_dir_name(cwd: Path) -> str:
    """Encode a working directory the way pi names its session bucket.

    Measured against pi 0.74.2 on Windows (2026-08-10): pi always wraps the whole
    absolute path, with ``/``, ``\\`` and ``:`` collapsed to ``-``, in a leading
    and trailing ``--``. It does this for every location, including paths under
    the user's home directory:

    - ``D:\\PROJECTS\\swe-mux``            -> ``--D--PROJECTS-swe-mux--``
    - ``C:\\Users\\me\\.mux-probe\\demo``  -> ``--C--Users-me-.mux-probe-demo--``

    This is deliberately *not* shared with :mod:`.omp`. oh-my-pi buckets a
    home-relative or temp-relative directory under a shortened scope-prefixed
    name and only falls back to the absolute form outside them, so reusing its
    encoder would look in the wrong directory for every pi session started under
    ``$HOME`` — the common case, and one that fails silently by finding nothing.
    """
    try:
        canonical = cwd.resolve(strict=False)
    except OSError:
        canonical = Path(os.path.abspath(cwd))
    flattened = re.sub(r"[/\\:]", "-", str(canonical).lstrip("/\\"))
    return f"--{flattened}--"


class PiAdapter(BackendAdapter):
    """Adapter for the upstream pi coding agent (`earendil-works/pi`).

    pi and oh-my-pi share an ancestor, so the session file is the same shape: a
    JSONL tree of ``id``/``parentId`` entries under a ``{"type":"session"}``
    header. Three measured differences drive everything specific here — the
    bucket encoder above, the absence of any terminal breadcrumb, and
    ``parentSession`` in place of omp's ``previousSessionFiles`` array.
    """

    name = "pi"
    reports_conversation_rollover = True
    assigns_conversation_id = False
    resolves_transcript_by_cwd = True

    def __init__(
        self,
        default_exe: str = "pi",
        default_args: list[str] | None = None,
        *,
        data_home: Path | None = None,
        command_resolver: Callable[[str], tuple[str, tuple[str, ...]]] | None = None,
    ) -> None:
        self.default_exe = default_exe
        self.default_args = default_args or []
        self._data_home = data_home
        # npm installs pi as a `.cmd` batch shim on Windows, which ConPTY cannot
        # execute. Without a resolver the pane opens and immediately dies with
        # "The system cannot find the file specified".
        self.command_resolver = command_resolver
        self._model_context_windows: dict[tuple[str, str], int] | None = None

    def _resolve(self, executable: str) -> tuple[str, tuple[str, ...]]:
        return self.command_resolver(executable) if self.command_resolver else (executable, ())

    @property
    def data_home(self) -> Path:
        return self._data_home or descriptor("pi").data_home()

    # ------------------------------------------------------------------ launch

    def _with_hook(self, args: list[str]) -> list[str]:
        target = str(pi_hook_path())
        if target in args:
            return args
        return ["--extension", target, *args]

    def spawn_spec(self, sid: str, opts: SpawnOptions) -> SpawnSpec:
        del sid
        executable, prefix = self._resolve(opts.exe or self.default_exe)
        return SpawnSpec(
            executable,
            (*prefix, *self._with_hook([*self.default_args, *opts.args])),
            {},
        )

    def resume_spec(self, native_id: str, opts: SpawnOptions) -> SpawnSpec:
        # pi resumes by id or path through `--session`; `--resume` is its
        # interactive picker and would sit waiting for a keystroke.
        executable, prefix = self._resolve(opts.exe or self.default_exe)
        return SpawnSpec(
            executable,
            (
                *prefix,
                *self._with_hook(["--session", native_id, *self.default_args, *opts.args]),
            ),
            {},
        )

    def resume_continues_conversation(self, recorded_cwd: str, target_cwd: str) -> bool:
        # `--session` reopens the named file and appends to it wherever pi is
        # standing, so the resumed pane continues the same conversation.
        del recorded_cwd, target_cwd
        return True

    def graceful_exit_keys(self) -> str:
        # Ctrl+C clears an in-progress draft, Ctrl+D exits an empty editor.
        return "\x03\x04"

    def session_env(self, session_id: str) -> Mapping[str, str]:
        # pi has no terminal-id mechanism to seed, and it reads none of omp's
        # `PI_*` display knobs (measured: pi 0.74.2 reads only PI_CODING_AGENT,
        # PI_CODING_AGENT_DIR, PI_KEY, PI_OFFLINE, PI_PACKAGE_DIR,
        # PI_SHARE_VIEWER_URL, PI_SKIP_VERSION_CHECK, PI_TELEMETRY, PI_TIMING,
        # PI_CLEAR_ON_SHRINK, PI_HARDWARE_CURSOR, PI_STARTUP_BENCHMARK,
        # PI_VERSION). Sending omp's tuning here would be cargo cult.
        del session_id
        return {}

    def configure(self, executable: str, args: list[str]) -> None:
        self.default_exe = executable
        self.default_args = list(args)

    def cleanup(self, session_id: str) -> None:
        # Nothing is materialized per session: the extension is a checked-in
        # file passed by path, and pi has no MCP surface to register.
        del session_id

    def media_reference(self, path: Path) -> str:
        return str(path)

    # -------------------------------------------------------------- transcript

    def _session_root(self) -> Path:
        return self.data_home / "sessions"

    def _session_files(self) -> list[Path]:
        try:
            return list(self._session_root().glob("*/*.jsonl"))
        except OSError:
            return []

    def _bucket(self, cwd: Path) -> Path:
        return self._session_root() / pi_session_dir_name(cwd)

    @staticmethod
    def _newest(matches: list[tuple[float, int, Path]]) -> Path | None:
        return max(matches)[2] if matches else None

    def _stat_key(self, path: Path) -> tuple[float, int, Path] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size, path)

    def transcript_path(self, native_id: str, cwd: Path) -> Path | None:
        bucket = self._bucket(cwd)
        matches: list[tuple[float, int, Path]] = []
        if bucket.is_dir():
            for candidate in bucket.glob("*.jsonl"):
                if self.transcript_native_id(candidate) != native_id:
                    continue
                key = self._stat_key(candidate)
                if key is not None:
                    matches.append(key)
        found = self._newest(matches)
        return found if found is not None else self.locate_transcript(native_id)

    def locate_transcript(self, native_id: str) -> Path | None:
        if not native_id:
            return None
        matches: list[tuple[float, int, Path]] = []
        for candidate in self._session_files():
            if self.transcript_native_id(candidate) != native_id:
                continue
            key = self._stat_key(candidate)
            if key is not None:
                matches.append(key)
        return self._newest(matches)

    def pending_transcript_path(self, session_id: str, current: Path) -> Path | None:
        # pi announces nothing ahead of materializing a file; the mux extension's
        # `session_start` report is what names a replacement conversation, and it
        # fires once the file exists.
        del session_id, current
        return None

    def transcript_native_id(self, path: Path) -> str | None:
        header = session_header(path)
        if header is not None:
            value = header.get("id")
            if isinstance(value, str) and value:
                return value
        match = _SESSION_ID_FROM_NAME.search(path.name)
        return match.group("id") if match else None

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        recent: list[tuple[float, Path, str]] = []
        bucket = self._bucket(cwd)
        if not bucket.is_dir():
            return recent
        for path in bucket.glob("*.jsonl"):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified + 2 < created_at:
                continue
            header = session_header(path)
            if header is None:
                continue
            native_id = header.get("id")
            header_cwd = header.get("cwd")
            if not isinstance(native_id, str) or not native_id:
                continue
            if not isinstance(header_cwd, str):
                continue
            if _same_path(header_cwd, cwd):
                recent.append((modified, path, native_id))
        return recent

    async def await_transcript(
        self, native_id: str, cwd: Path, created_at: float, stop: asyncio.Event
    ) -> Path | None:
        while not stop.is_set():
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

    # ------------------------------------------------------------ measurement

    def model_context_window(self, provider: str, model: str) -> int:
        if self._model_context_windows is None:
            self._model_context_windows = self._load_model_context_windows()
        exact = self._model_context_windows.get((provider, model))
        if exact:
            return exact
        # A model reached through a custom or subscription-backed provider keeps
        # its catalogued id while carrying a provider id the catalog has never
        # heard of, so an exact (provider, model) miss is routine rather than
        # exceptional. Fall back to the id alone, and only when every provider
        # that offers it agrees on the window — a disagreement means the number
        # is genuinely provider-dependent and guessing one would render a
        # confident, wrong context percentage.
        windows = {
            window
            for (_provider, catalogued), window in self._model_context_windows.items()
            if catalogued == model
        }
        return windows.pop() if len(windows) == 1 else 0

    def _catalog_candidates(self) -> list[Path]:
        """Places the bundled pi-ai model catalog can sit for an installed pi.

        Resolution walks out from the configured executable because pi ships the
        catalog inside its own dependency tree rather than writing a cache into
        its data home the way oh-my-pi's `models.db` does. Nothing here reads the
        network or runs pi.
        """
        roots: list[Path] = []
        # `default_exe` is normally the bare name `pi` (that is what the config
        # default and the descriptor both carry), so resolving it as a path would
        # anchor every candidate at the daemon's cwd and find nothing — measured:
        # the catalog loaded zero entries in the daemon while loading fine from a
        # shell that happened to sit in the right directory. Resolve on PATH
        # first, and keep the literal value for an explicitly configured path.
        # `which_real`, never `shutil.which`: the daemon's PATH begins with its
        # own generated agent shims, so a plain lookup answers
        # `~/.mux/bin/pi.cmd` and anchors every candidate inside mux's shim
        # directory. Measured: the catalog loaded zero entries for exactly this
        # reason. `which_real` is the codebase's existing guard against
        # resolving back into a mux shim.
        located = which_real(self.default_exe) if not os.path.isabs(self.default_exe) else None
        exe = Path(located or self.default_exe)
        try:
            exe_dir = exe.resolve(strict=False).parent
        except OSError:
            exe_dir = exe.parent
        for base in (exe_dir, exe_dir.parent):
            roots.append(base / "node_modules" / "@earendil-works" / "pi-coding-agent")
            roots.append(base / "lib" / "node_modules" / "@earendil-works" / "pi-coding-agent")
        return [root / "node_modules" / _MODEL_CATALOG_RELATIVE for root in roots]

    @staticmethod
    def _harvest(node: Any, into: dict[tuple[str, str], int]) -> None:
        """Collect every ``{id, provider, contextWindow}`` object in a JSON tree.

        Walked generically rather than at a fixed depth. pi 0.84.1 nests
        provider file -> api group -> model, but the nesting has already changed
        once (0.74.2 kept the whole catalog in one JS object literal), so a
        structural walk survives the next reshuffle where an indexed path would
        silently start returning nothing.
        """
        if isinstance(node, dict):
            model = node.get("id")
            provider = node.get("provider")
            window = node.get("contextWindow")
            if (
                isinstance(model, str)
                and isinstance(provider, str)
                and isinstance(window, int)
                and not isinstance(window, bool)
                and window > 0
            ):
                into[(provider, model)] = window
            for value in node.values():
                PiAdapter._harvest(value, into)
        elif isinstance(node, list):
            for value in node:
                PiAdapter._harvest(value, into)

    def _load_model_context_windows(self) -> dict[tuple[str, str], int]:
        result: dict[tuple[str, str], int] = {}
        for candidate in self._catalog_candidates():
            if not candidate.is_file():
                continue
            # pi >= 0.75 ships the catalog as JSON under `providers/data/`, and
            # `models.generated.js` is reduced to a list of imports. Reading the
            # JSON is both more robust than parsing JS and how the newer layout
            # actually stores the data.
            data_dir = candidate.parent / "providers" / "data"
            for path in sorted(data_dir.glob("*.json")) if data_dir.is_dir() else ():
                try:
                    self._harvest(json.loads(path.read_text(encoding="utf-8")), result)
                except (OSError, json.JSONDecodeError):
                    continue
            if result:
                return result
            # pi <= 0.74 kept every model in one generated JS object literal.
            try:
                source = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
            for match in _MODEL_ENTRY.finditer(source):
                window = int(match.group("window"))
                if window > 0:
                    result[(match.group("provider"), match.group("model"))] = window
            if result:
                return result
        # An unknown window is reported as unknown. Guessing a default would
        # render as a plausible context percentage for every model pi adds.
        return {}


def _same_path(left: Path | str, right: Path | str) -> bool:
    def canonical(value: Path | str) -> str:
        path = Path(value)
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = Path(os.path.abspath(path))
        return os.path.normcase(str(resolved))

    return canonical(left) == canonical(right)


def pi_session_header(path: Path) -> dict[str, Any] | None:
    """Re-exported for consumers that read a pi header without an adapter."""
    return session_header(path)


def parent_session_path(header: Mapping[str, Any] | None) -> Path | None:
    """The file this conversation was forked from, if any.

    pi records a single ``parentSession`` path on `/fork` and `/clone`, where omp
    keeps an append-only ``previousSessionFiles`` array. Both answer "where did
    this conversation come from", but only omp's can describe a chain.
    """
    if not header:
        return None
    value = header.get("parentSession")
    return Path(value) if isinstance(value, str) and value else None


__all__ = [
    "PiAdapter",
    "parent_session_path",
    "pi_hook_path",
    "pi_session_dir_name",
    "pi_session_header",
]
