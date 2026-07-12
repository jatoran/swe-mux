from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import uuid


def _promote(backend: str, native_id: str) -> None:
    url, secret = os.environ.get("MUX_PROMOTE_URL"), os.environ.get("MUX_HOOK_SECRET")
    if not url or not secret:
        return
    request = urllib.request.Request(
        url,
        data=json.dumps({"backend": backend, "native_id": native_id}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Mux-Hook-Secret": secret},
    )
    try:
        urllib.request.urlopen(request, timeout=3).close()
    except OSError:
        pass


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
    return exe, args, native_id


def _codex(args: list[str]) -> tuple[str, list[str], str]:
    exe = os.environ.get("MUX_CODEX_EXE", "codex.exe")
    native_id = args[1] if len(args) > 1 and args[0] == "resume" else str(uuid.uuid4())
    if not any("notify=" in arg for arg in args):
        notify = [sys.executable, "-m", "swe_mux.hook_client", "codex_notify"]
        args = ["-c", f"notify={json.dumps(notify)}", *args]
    return exe, args, native_id


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"claude", "codex"}:
        raise SystemExit("usage: python -m swe_mux.agent_launcher claude|codex [args...]")
    backend, args = sys.argv[1], sys.argv[2:]
    exe, command_args, native_id = _claude(args) if backend == "claude" else _codex(args)
    _promote(backend, native_id)
    try:
        raise SystemExit(subprocess.call([exe, *command_args]))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
