from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def command_argv(payload: dict[str, Any]) -> list[str]:
    command = str(payload["command"])
    resolved = shutil.which(command) or command
    args = [str(item) for item in payload.get("args", [])]
    argv = [resolved, *args]
    if os.name == "nt" and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(argv),
        ]
    return argv


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m swe_mux.action_runner PAYLOAD")
    payload = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode())
    if not isinstance(payload, dict) or not isinstance(payload.get("env", {}), dict):
        raise SystemExit("invalid Project Action payload")
    environment = {**os.environ, **{str(key): str(value) for key, value in payload["env"].items()}}
    try:
        process = subprocess.Popen(
            command_argv(payload),
            cwd=str(payload["cwd"]),
            env=environment,
        )
        return process.wait()
    except FileNotFoundError as exc:
        print(f"Project Action executable not found: {exc.filename}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
