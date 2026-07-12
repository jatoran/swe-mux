from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "hook"
    url = os.environ.get("MUX_HOOK_URL")
    secret = os.environ.get("MUX_HOOK_SECRET")
    if not url or not secret:
        return
    raw = sys.stdin.read() or (sys.argv[2] if len(sys.argv) > 2 else "")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}
    if event == "codex_notify" and isinstance(payload, dict) and payload.get("type"):
        event = str(payload["type"])
    body = json.dumps({"event": event, "payload": payload}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Mux-Hook-Secret": secret},
    )
    try:
        urllib.request.urlopen(request, timeout=2).close()
    except OSError:
        pass


if __name__ == "__main__":
    main()
