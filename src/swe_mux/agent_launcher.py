from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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


def _notify_lifecycle(url_name: str, payload: dict[str, str]) -> None:
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
        urllib.request.urlopen(request, timeout=3).close()
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
                Path(root) / session_id, opencode_plugin_path()
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


def _child_stdio() -> dict[str, Any]:
    """Std streams to forward explicitly to the agent child when frozen.

    A windowed parent's children derive default stdio from their console, not
    from the pipes or terminal that invoked the shim — so daemon-driven calls
    (`codex login` capture) would lose all output. Forward this process's real
    handles when it has them; an incomplete set falls back to the defaults.
    """
    if not getattr(sys, "frozen", False):
        return {}
    kwargs: dict[str, Any] = {}
    for name, stream in (("stdin", sys.stdin), ("stdout", sys.stdout), ("stderr", sys.stderr)):
        try:
            if stream is None or stream.fileno() < 0:
                return {}
        except (OSError, ValueError, AttributeError):
            return {}
        kwargs[name] = stream
    return kwargs


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


def _launch(executable: str, args: list[str]) -> int:
    """Run native executables directly and Windows batch shims through COMSPEC."""
    executable = _resolve_agent_executable(executable)
    if is_mux_shim(executable):
        raise SystemExit(
            f"swe-mux: no real {Path(executable).stem} CLI found on PATH; "
            f"refusing to relaunch the mux shim {executable}"
        )
    executable_path = Path(executable)
    stdio = _child_stdio()
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
                return subprocess.call([node, str(codex_js), *args], **stdio)
    if os.name == "nt" and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline([executable, *args])
        return subprocess.call(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line], **stdio
        )
    return subprocess.call([executable, *args], **stdio)


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
    _attach_parent_console()
    exe, command_args, native_id = _build_launch(backend, args)
    _promote(backend, native_id)
    exit_code = 130
    try:
        exit_code = _launch(exe, command_args)
    except KeyboardInterrupt:
        pass
    finally:
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
