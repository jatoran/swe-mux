from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path


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


def _promote(backend: str, native_id: str) -> None:
    _notify_lifecycle(
        "MUX_PROMOTE_URL", {"backend": backend, "native_id": native_id}
    )


def _demote(backend: str, native_id: str) -> None:
    _notify_lifecycle(
        "MUX_DEMOTE_URL", {"backend": backend, "native_id": native_id}
    )


def _value_after(args: list[str], flag: str) -> str | None:
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def _claude(args: list[str]) -> tuple[str, list[str], str]:
    exe = os.environ.get("MUX_CLAUDE_EXE", "claude.exe")
    native_id = _value_after(args, "--session-id") or _value_after(args, "--resume")
    if not native_id:
        native_id = str(uuid.uuid4())
        args = ["--session-id", native_id, *args]
    settings = os.environ.get("MUX_CLAUDE_SETTINGS")
    if settings and "--settings" not in args:
        args = ["--settings", settings, *args]
    args = [*json.loads(os.environ.get("MUX_CLAUDE_ARGS", "[]")), *args]
    return exe, args, native_id


def _codex(args: list[str]) -> tuple[str, list[str], str]:
    exe = os.environ.get("MUX_CODEX_EXE", "codex.exe")
    native_id = args[1] if len(args) > 1 and args[0] == "resume" else str(uuid.uuid4())
    args = [*json.loads(os.environ.get("MUX_CODEX_ARGS", "[]")), *args]
    if not any("notify=" in arg for arg in args):
        notify = [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"]
        args = ["-c", f"notify={json.dumps(notify)}", *args]
    return exe, args, native_id


def _launch(executable: str, args: list[str]) -> int:
    """Run native executables directly and Windows batch shims through COMSPEC."""
    resolved = shutil.which(executable)
    if not resolved and os.name == "nt" and executable.lower().endswith(".exe"):
        shim_dir = os.environ.get("MUX_SHIM_DIR")
        search_entries: list[str] = []
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if shim_dir and Path(entry) == Path(shim_dir):
                continue
            marker = Path(entry) / "codex.cmd"
            try:
                if marker.is_file() and "swe_mux.agent_launcher" in marker.read_text(
                    encoding="utf-8", errors="ignore"
                ):
                    continue
            except OSError:
                pass
            search_entries.append(entry)
        resolved = shutil.which(Path(executable).stem, path=os.pathsep.join(search_entries))
    executable = resolved or executable
    executable_path = Path(executable)
    if (
        os.name == "nt"
        and executable_path.name.casefold() in {"codex.cmd", "codex.bat"}
    ):
        # npm's batch shim cannot preserve Codex's JSON-valued `-c notify=...`
        # argument through cmd.exe. Launch its underlying JS entrypoint directly.
        codex_js = (
            executable_path.parent
            / "node_modules"
            / "@openai"
            / "codex"
            / "bin"
            / "codex.js"
        )
        if codex_js.is_file():
            bundled_node = executable_path.parent / "node.exe"
            node = str(bundled_node) if bundled_node.is_file() else shutil.which("node")
            if node:
                return subprocess.call([node, str(codex_js), *args])
    if os.name == "nt" and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        command_line = subprocess.list2cmdline([executable, *args])
        return subprocess.call(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]
        )
    return subprocess.call([executable, *args])


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"claude", "codex"}:
        raise SystemExit("usage: python -m swe_mux.agent_launcher claude|codex [args...]")
    backend, args = sys.argv[1], sys.argv[2:]
    exe, command_args, native_id = _claude(args) if backend == "claude" else _codex(args)
    _promote(backend, native_id)
    exit_code = 130
    try:
        exit_code = _launch(exe, command_args)
    except KeyboardInterrupt:
        pass
    finally:
        _demote(backend, native_id)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
