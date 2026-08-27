from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, assert_never

from .adapters.codex import codex_lifecycle_hook_args
from .adapters.omp import materialize_mux_extension, retire_mux_extension, with_mux_hook
from .adapters.opencode import materialize_mux_config, opencode_plugin_path, retire_mux_config
from .adapters.pi import pi_hook_path
from .codex_tui import with_scrollback_safe_tui
from .harness import Backend, agent_harnesses, descriptor, is_agent_harness, require_backend
from .shim_paths import is_mux_shim, path_without_shim_dirs
from .spawn_contract import AGENT_FORCE_COLOR_ENV

#: Reports are point telemetry on a launch path a human is waiting on, so the
#: budget is a localhost round trip rather than the lifecycle notifications'
#: three seconds. A dropped report costs a diagnostic; a slow one costs startup.
_REPORT_TIMEOUT_SECONDS = 1.5


def _notify_lifecycle(url_name: str, payload: dict[str, Any], *, timeout: float = 3.0) -> None:
    url, secret = os.environ.get(url_name), os.environ.get("MUX_HOOK_SECRET")
    if not url or not secret:
        return
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Mux-Hook-Secret": secret},
    )
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
    except OSError:
        pass


def _promote(backend: Backend, native_id: str) -> None:
    _notify_lifecycle(
        "MUX_PROMOTE_URL", {"backend": backend, "native_id": native_id, "cwd": os.getcwd()}
    )


def _demote(backend: Backend, native_id: str) -> None:
    _notify_lifecycle("MUX_DEMOTE_URL", {"backend": backend, "native_id": native_id})


def _value_after(args: list[str], flag: str) -> str | None:
    """The token after ``flag``, only when it is a value rather than the next flag.

    ``--resume`` takes an *optional* id: bare ``claude --resume`` opens the picker,
    and reading the next token unconditionally captures whatever followed it (a
    flag, a prompt) as a conversation id.
    """
    try:
        index = args.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(args):
        return None
    candidate = args[index + 1]
    return None if candidate.startswith("-") else candidate


_CLAUDE_SESSION_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Every documented form that makes the CLI attach to an existing conversation.
# `--session-id` is mutually exclusive with all of them (the CLI exits 1), so the
# shim must recognize each one rather than only the long resume spelling.
_CLAUDE_RESUME_FLAGS = ("--resume", "-r")
_CLAUDE_CONTINUE_FLAGS = ("--continue", "-c")


def _env_prefix(name: str) -> str:
    return f"MUX_{name.upper().replace('-', '_')}"


def _claude(
    args: list[str], *, name: str = "claude", default_executable: str = "claude.exe"
) -> tuple[str, list[str], str]:
    prefix = _env_prefix(name)
    exe = os.environ.get(f"{prefix}_EXE", default_executable)
    native_id = _value_after(args, "--session-id") or ""
    resuming = any(flag in args for flag in (*_CLAUDE_RESUME_FLAGS, *_CLAUDE_CONTINUE_FLAGS))
    if not native_id:
        for flag in _CLAUDE_RESUME_FLAGS:
            value = _value_after(args, flag)
            if value and _CLAUDE_SESSION_ID.fullmatch(value):
                native_id = value
                break
    if not native_id and not resuming:
        native_id = str(uuid.uuid4())
        args = ["--session-id", native_id, *args]
    if not _CLAUDE_SESSION_ID.fullmatch(native_id):
        # `--continue`, `--resume` with no id, or `-r <search term>`: the CLI picks
        # the conversation, so the shim does not know its id and must not claim
        # one. Promotion reports it empty and the daemon binds the transcript from
        # the CLI's own SessionStart hook instead of guessing.
        native_id = ""
    settings = os.environ.get(f"{prefix}_SETTINGS")
    if settings and "--settings" not in args:
        args = ["--settings", settings, *args]
    # Register the mux MCP server only when this session actually holds an MCP
    # identity; a registration without a token would just 401 inside the CLI.
    mcp_config = os.environ.get(f"{prefix}_MCP_CONFIG")
    if mcp_config and os.environ.get("MUX_MCP_TOKEN") and "--mcp-config" not in args:
        args = ["--mcp-config", mcp_config, *args]
    args = [*json.loads(os.environ.get(f"{prefix}_ARGS", "[]")), *args]
    return exe, args, native_id


def _codex(
    args: list[str], *, name: str = "codex", default_executable: str = "codex.exe"
) -> tuple[str, list[str], str]:
    prefix = _env_prefix(name)
    exe = os.environ.get(f"{prefix}_EXE", default_executable)
    native_id = args[1] if len(args) > 1 and args[0] == "resume" else str(uuid.uuid4())
    args = with_scrollback_safe_tui(
        [*json.loads(os.environ.get(f"{prefix}_ARGS", "[]")), *args]
    )
    if not any("notify=" in arg for arg in args):
        notify = [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"]
        args = ["-c", f"notify={json.dumps(notify)}", *args]
    args = [*codex_lifecycle_hook_args(args), *args]
    mcp_url = os.environ.get("MUX_MCP_URL")
    if (
        mcp_url
        and os.environ.get("MUX_MCP_TOKEN")
        and not any("mcp_servers.mux" in arg for arg in args)
    ):
        args = [
            "-c",
            f'mcp_servers.mux.url="{mcp_url}"',
            "-c",
            'mcp_servers.mux.bearer_token_env_var="MUX_MCP_TOKEN"',
            *args,
        ]
    return exe, args, native_id


def _omp(args: list[str]) -> tuple[str, list[str], str]:
    prefix = _env_prefix("omp")
    exe = os.environ.get(f"{prefix}_EXE", "omp")
    configured = json.loads(os.environ.get(f"{prefix}_ARGS", "[]"))
    extension: Path | None = None
    root = os.environ.get("MUX_OMP_EXTENSION_ROOT")
    session_id = os.environ.get("MUX_SESSION_ID")
    if root and session_id:
        extension = materialize_mux_extension(
            Path(root) / session_id,
            mcp_url=os.environ.get("MUX_MCP_URL"),
            mcp_token=os.environ.get("MUX_MCP_TOKEN"),
        )
    return exe, with_mux_hook([*configured, *args], extension), ""


def _pi(args: list[str]) -> tuple[str, list[str], str]:
    prefix = _env_prefix("pi")
    exe = os.environ.get(f"{prefix}_EXE", "pi")
    configured = json.loads(os.environ.get(f"{prefix}_ARGS", "[]"))
    hook = str(pi_hook_path())
    merged = [*configured, *args]
    # pi mints its own conversation id, so the shim promotes with an empty
    # native id and the extension's `session_start` report establishes identity.
    if hook in merged:
        return exe, merged, ""
    return exe, ["--extension", hook, *merged], ""


def _opencode(args: list[str]) -> tuple[str, list[str], str]:
    prefix = _env_prefix("opencode")
    exe = os.environ.get(f"{prefix}_EXE", "opencode")
    configured = json.loads(os.environ.get(f"{prefix}_ARGS", "[]"))
    root = os.environ.get("MUX_OPENCODE_CONFIG_ROOT")
    session_id = os.environ.get("MUX_SESSION_ID")
    if root and session_id:
        try:
            config = materialize_mux_config(
                Path(root) / session_id,
                opencode_plugin_path(),
                mcp_url=os.environ.get("MUX_MCP_URL"),
                mcp_token=os.environ.get("MUX_MCP_TOKEN"),
            )
        except OSError:
            # Forfeit the plugin, never the pane.
            pass
        else:
            # opencode takes no per-launch plugin flag, so the plugin is added
            # through an extra *merged* config layer rather than by replacing
            # OPENCODE_CONFIG_DIR, which would hide the user's own agents,
            # commands, and auth.
            os.environ["OPENCODE_CONFIG"] = str(config)
    return exe, [*configured, *args], ""


class _ShimTrace:
    """Everything one shim invocation has to say about itself.

    The 2026-08-27 console-contention incident had to be diagnosed from a live
    `Win32_Process` walk and a PTY tail, because this process emitted nothing at
    all: `_promote`/`_demote` are fire-and-forget with `except OSError: pass`, so
    there was no record that the wrapper had ever started, what it resolved, which
    child it spawned, or how it died. The single most useful fact - "the wrapper is
    gone and its child is not" - was not recoverable after the fact from anything
    mux stored.

    One instance per process, reported at three moments (`started`, `child_started`,
    `exited`). It carries no argument *values*: a prompt can be on this command
    line, and diagnosing a launch chain needs the shape of the argv, not its
    contents.
    """

    def __init__(self) -> None:
        self.backend = ""
        self.child_pid: int | None = None
        self.exit_reported = False
        self.started_at = time.monotonic()
        self.exit_path = "normal"

    def report(self, kind: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "kind": kind,
            "backend": self.backend,
            "shim_pid": os.getpid(),
            "elapsed_ms": round((time.monotonic() - self.started_at) * 1000, 1),
            **fields,
        }
        _notify_lifecycle("MUX_SHIM_URL", payload, timeout=_REPORT_TIMEOUT_SECONDS)

    def report_exit(self, exit_code: int | None, *, path: str | None = None) -> None:
        """Report the wrapper's own end, exactly once, from whichever path got here.

        Called from the normal return, from `atexit`, and from the console control
        handler, because the interesting exits are the ones that do not run a
        `finally`. Idempotent so those three cannot triple-report.
        """
        if self.exit_reported:
            return
        self.exit_reported = True
        outlived = None
        if self.child_pid is not None:
            outlived = _pid_alive(self.child_pid)
        self.report(
            "exited",
            exit_code=exit_code,
            child_pid=self.child_pid,
            # The defect this whole module exists for: the wrapper stopped waiting
            # while its agent kept the console. True here is contention, already
            # proven, before the daemon has to infer it from a shell prompt.
            child_outlived_shim=outlived,
            exit_path=path or self.exit_path,
        )


TRACE = _ShimTrace()


def _pid_alive(pid: int) -> bool | None:
    """Whether ``pid`` is still running, or ``None`` when that cannot be answered.

    Deliberately not psutil: this runs on the launch path of a frozen bundle where
    importing psutil is a measurable cost, and the question is one syscall. ``None``
    rather than a guess, because the caller reports it verbatim.
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION: enough to read the exit code of a
            # process this one may no longer be the parent of.
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return None
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError, ValueError):
            return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _process_start_delay_ms() -> float | None:
    """Milliseconds between this process starting and reaching :func:`main`.

    On the frozen desktop app this is the PyInstaller bootstrap, which is the
    dominant cost of launching an agent by typing its name in a shell pane -
    measured 2026-08-27 at ~300 ms warm and 43 s cold, the cold case being
    Defender scanning the bundle. Neither number is visible anywhere today, so a
    user reporting "typing `claude` hangs" has nothing to point at.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            kernel32.GetCurrentProcess(),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        now = wintypes.FILETIME()
        kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))

        def _ticks(value: Any) -> int:
            return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

        return round((_ticks(now) - _ticks(creation)) / 10_000, 1)
    except (OSError, AttributeError, ValueError):
        return None


# Console control events, and what each one means for a process whose only job is
# to stay out of the way until its child is finished.
_CTRL_C_EVENT = 0
_CTRL_BREAK_EVENT = 1
_CTRL_CLOSE_EVENT = 2
#: Handled here and swallowed. The agent is attached to the same console and gets
#: its own copy; it is the only participant that should act on one.
_HELD_EVENTS = frozenset({_CTRL_C_EVENT, _CTRL_BREAK_EVENT})

#: Kept alive for the process lifetime. A `ctypes` callback that is garbage
#: collected while Windows still holds the pointer is a crash, not a no-op.
_CONSOLE_HANDLER: Any = None


def _hold_console_signals() -> None:
    """Refuse to be the link in the launch chain that unblocks first.

    A console control event is delivered to *every* process attached to a console,
    not to a foreground job the way a POSIX terminal would. In the shim chain
    (`pwsh -> cmd.exe -> swe-mux.exe -> claude.exe`) the agent is the only one that
    treats a `CTRL_C_EVENT` as anything other than a reason to stop waiting: Claude
    clears its composer and keeps running, while an unprotected Python wrapper takes
    `KeyboardInterrupt` out of `Popen.wait` and exits. The shell then prints a
    prompt over a still-running agent and both read the console
    (`console_contention.py`).

    **This is defence in depth, not the fix for a measured failure.** Injecting
    `0x03` into mux's own pseudoconsole was measured on 2026-08-27 *not* to produce
    a `CTRL_C_EVENT` at all - winpty/OpenConsole headless passes it through as a key
    event - so this is not how the observed incident happened, and
    `tests/test_shim_console_handoff.py` skips rather than claiming otherwise on
    such a host. It is kept because the chain is reachable from consoles mux does
    not own (a real terminal, an RDP console, a future backend), because Ctrl+C on
    this path is not an edge case but how the agent's own users clear a draft, and
    because holding it costs one callback.

    `CTRL_CLOSE_EVENT` and the logoff/shutdown events are deliberately *not* held:
    those are real terminations with an OS-enforced deadline, and swallowing one
    would only make the process die less gracefully. The handler reports the
    wrapper's exit on that path instead, which is the one exit that never runs a
    `finally`.
    """
    global _CONSOLE_HANDLER
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        def _handler(event: int) -> bool:
            if event in _HELD_EVENTS:
                return True
            if event == _CTRL_CLOSE_EVENT:
                TRACE.report_exit(None, path="console_close")
            return False

        prototype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        _CONSOLE_HANDLER = prototype(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CONSOLE_HANDLER, True)
    except (OSError, AttributeError, ValueError):
        # A host that will not take the handler still launches the agent; it just
        # keeps the old failure mode, which the daemon now detects either way.
        _CONSOLE_HANDLER = None


def _attach_parent_console() -> None:
    """Join the invoking shim's console before spawning the agent.

    The shims run the frozen swe-mux.exe, a windowed build with no console.
    Console children of a console-less parent each get a fresh visible console
    window and the agent TUI renders there instead of the terminal the user
    typed into. Attaching to the parent cmd.exe's console restores normal
    inheritance; when there is no parent console (GUI launch) this is a no-op.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    if not kernel32.GetConsoleWindow():
        kernel32.AttachConsole(0xFFFFFFFF)  # ATTACH_PARENT_PROCESS


def _console_facts() -> dict[str, Any]:
    """What this process can see of the console it is about to hand to the agent.

    Reported once, at `started`. The frozen build is a GUI-subsystem image
    (measured: PE subsystem 2) that has to `AttachConsole` its way back into the
    pane's pseudoconsole, so "did that work" is the first question of any launch
    incident and was previously unanswerable.
    """
    facts: dict[str, Any] = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "console_window": None,
        "std_handles": {},
    }
    if os.name != "nt":
        return facts
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        facts["console_window"] = bool(kernel32.GetConsoleWindow())
        kinds = {0: "unknown", 1: "disk", 2: "char", 3: "pipe"}
        for name, std_id in (("stdin", -10), ("stdout", -11), ("stderr", -12)):
            handle = kernel32.GetStdHandle(std_id)
            if not handle or handle == ctypes.c_void_p(-1).value:
                facts["std_handles"][name] = "invalid"
                continue
            mode = ctypes.c_uint32()
            is_console = bool(
                kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode))
            )
            kind = kinds.get(kernel32.GetFileType(ctypes.c_void_p(handle)), "unknown")
            facts["std_handles"][name] = f"{kind}{'/console' if is_console else ''}"
    except (OSError, AttributeError, ValueError):
        pass
    return facts


def argv_shape(args: list[str]) -> list[str]:
    """The flag names on a command line, without any of their values.

    Enough to tell `--session-id` present from absent, or a `--resume` launch from
    a fresh one, which is what a launch incident turns on. A value is never
    included: an agent invocation can carry a prompt, and this leaves the machine.
    """
    return [item for item in args if item.startswith("-")]


def _child_stdio() -> tuple[dict[str, Any], str]:
    """Std streams to forward explicitly to the agent child when frozen.

    A windowed parent's children derive default stdio from their console, not
    from the pipes or terminal that invoked the shim — so daemon-driven calls
    (`codex login` capture) would lose all output. Forward this process's real
    handles when it has them; an incomplete set falls back to the defaults.

    Returns the kwargs *and* which of the three cases produced them, because
    ``{}`` means two very different things - "not frozen, ordinary inheritance" and
    "frozen with no usable streams, so the child inherits the console instead" -
    and the launch telemetry has to be able to tell them apart. A windowed
    PyInstaller build has ``sys.stdout is None``, so the frozen path takes the
    fallback on exactly the configuration this matters for.
    """
    if not getattr(sys, "frozen", False):
        return {}, "inherit_unfrozen"
    kwargs: dict[str, Any] = {}
    for name, stream in (("stdin", sys.stdin), ("stdout", sys.stdout), ("stderr", sys.stderr)):
        try:
            if stream is None or stream.fileno() < 0:
                return {}, "inherit_no_streams"
        except (OSError, ValueError, AttributeError):
            return {}, "inherit_no_streams"
        kwargs[name] = stream
    return kwargs, "forwarded"


def child_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the real CLI is launched with.

    Adds the colour-forcing pair every agent harness gets when mux spawns it
    directly (`spawn_contract.AGENT_FORCE_COLOR_ENV`). A shell session deliberately
    does *not* get it - a shell must keep pipe semantics, so a forced flag would
    leak ANSI into ``cmd > file`` - and that environment is fixed at spawn, so
    promotion cannot repair it afterwards. The shim is the one place that knows an
    agent is being launched into a shell's environment, which makes it the right
    place to add it: without this, an agent started by typing its name renders
    monochrome while the same agent from the Run menu does not.
    """
    env = dict(os.environ if environ is None else environ)
    env.update(AGENT_FORCE_COLOR_ENV)
    return env


def _resolve_agent_executable(executable: str) -> str:
    """Resolve the configured CLI, never landing back on a mux agent shim.

    ``MUX_*_EXE`` can point at ``~/.mux/bin``'s own shim when the daemon that
    wired it had the shim directory on its PATH (a daemon relaunched from
    inside a session inherits exactly that). Launching it would recurse:
    shim -> swe-mux.exe -> shim, spawning a console window per cycle.
    """
    resolved = shutil.which(executable)
    if resolved and not is_mux_shim(resolved):
        return resolved
    if resolved:
        real = shutil.which(Path(executable).stem, path=path_without_shim_dirs())
        if real and not is_mux_shim(real):
            return real
        return resolved
    if os.name == "nt" and executable.lower().endswith(".exe"):
        # npm-installed CLIs commonly expose only codex.cmd/claude.cmd even
        # when the mux's compatibility default still names an .exe.
        real = shutil.which(Path(executable).stem, path=path_without_shim_dirs())
        if real:
            return real
    return executable


def _run_child(argv: list[str], stdio: dict[str, Any], stdio_mode: str) -> int:
    """Spawn the real CLI, publish its pid, and wait for it.

    `subprocess.call` was the historical spelling and is not enough any more: the
    daemon needs the child's pid *while it runs* to answer "is the agent still
    alive and still under this pane's PTY root" when a shell prompt appears
    (`console_contention.py`). That question is the difference between demoting a
    session whose agent has exited and reporting one whose shell has taken the
    console back, and it cannot be answered after the fact.

    The wait is deliberately uninterruptible from the console: `_hold_console_signals`
    has already swallowed Ctrl+C, so the only ways out are the child exiting and the
    wrapper being killed.
    """
    process = subprocess.Popen(argv, env=child_environment(), **stdio)  # noqa: S603
    TRACE.child_pid = process.pid
    TRACE.report(
        "child_started",
        child_pid=process.pid,
        executable=argv[0],
        stdio_mode=stdio_mode,
    )
    return process.wait()


def _launch(executable: str, args: list[str]) -> int:
    """Run native executables directly and Windows batch shims through COMSPEC."""
    executable = _resolve_agent_executable(executable)
    if is_mux_shim(executable):
        raise SystemExit(
            f"swe-mux: no real {Path(executable).stem} CLI found on PATH; "
            f"refusing to relaunch the mux shim {executable}"
        )
    executable_path = Path(executable)
    stdio, stdio_mode = _child_stdio()
    if os.name == "nt" and executable_path.name.casefold() in {"codex.cmd", "codex.bat"}:
        # npm's batch shim cannot preserve Codex's JSON-valued `-c notify=...`
        # argument through cmd.exe. Launch its underlying JS entrypoint directly.
        codex_js = (
            executable_path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        )
        if codex_js.is_file():
            bundled_node = executable_path.parent / "node.exe"
            node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
            if node:
                return _run_child([node, str(codex_js), *args], stdio, stdio_mode)
    if os.name == "nt" and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline([executable, *args])
        return _run_child(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line],
            stdio,
            stdio_mode,
        )
    return _run_child([executable, *args], stdio, stdio_mode)


def _build_launch(backend: Backend, args: list[str]) -> tuple[str, list[str], str]:
    if backend == "shell":
        raise ValueError("shell does not use the agent launcher")
    if backend == "claude":
        pass
    elif backend == "codex":
        pass
    elif backend == "omp":
        return _omp(args)
    elif backend == "pi":
        return _pi(args)
    elif backend == "opencode":
        return _opencode(args)
    else:
        assert_never(backend)
    harness = descriptor(backend)
    if harness.adapter_family == "claude":
        if backend == "claude":
            return _claude(args)
        return _claude(args, name=backend, default_executable=harness.executable)
    if harness.adapter_family == "codex":
        if backend == "codex":
            return _codex(args)
        return _codex(args, name=backend, default_executable=harness.executable)
    if harness.adapter_family == "omp":
        return _omp(args)
    if harness.adapter_family == "pi":
        return _pi(args)
    if harness.adapter_family == "opencode":
        return _opencode(args)
    assert_never(harness.adapter_family)


def main() -> None:
    if len(sys.argv) < 2 or not is_agent_harness(sys.argv[1]):
        names = "|".join(descriptor_name for descriptor_name in sorted(agent_harnesses()))
        raise SystemExit(f"usage: python -m swe_mux.agent_launcher {names} [args...]")
    backend, args = require_backend(sys.argv[1]), sys.argv[2:]
    TRACE.backend = backend
    # Order matters. The console has to be joined before its state can be read,
    # the control handler has to be armed before the first byte reaches the CLI
    # (a Ctrl+C during a slow cold start is exactly the window this protects), and
    # the exit report has to be registered before anything can fail.
    _attach_parent_console()
    _hold_console_signals()
    atexit.register(lambda: TRACE.report_exit(None, path="atexit"))
    exe, command_args, native_id = _build_launch(backend, args)
    TRACE.report(
        "started",
        executable=exe,
        argv_flags=argv_shape(command_args),
        argv_count=len(command_args),
        native_id_assigned=bool(native_id),
        parent_pid=os.getppid(),
        boot_ms=_process_start_delay_ms(),
        **_console_facts(),
    )
    _promote(backend, native_id)
    exit_code = 130
    try:
        exit_code = _launch(exe, command_args)
    except KeyboardInterrupt:
        # Reachable only where `_hold_console_signals` could not arm (a non-Windows
        # host, or one that refused the handler). Recorded rather than swallowed
        # silently, because on Windows this path *is* the bug.
        TRACE.exit_path = "keyboard_interrupt"
    finally:
        TRACE.report_exit(exit_code, path=TRACE.exit_path)
        _demote(backend, native_id)
        if backend == "omp":
            root = os.environ.get("MUX_OMP_EXTENSION_ROOT")
            session_id = os.environ.get("MUX_SESSION_ID")
            if root and session_id:
                retire_mux_extension(Path(root), session_id)
        if backend == "opencode":
            config_root = os.environ.get("MUX_OPENCODE_CONFIG_ROOT")
            session_id = os.environ.get("MUX_SESSION_ID")
            if config_root and session_id:
                retire_mux_config(Path(config_root), session_id)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
