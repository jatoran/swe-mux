from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

# Terminal lifecycle events are the ones whose loss strands a session as
# "working"; when the POST cannot be delivered they are spooled to disk for the
# daemon to replay. Non-terminal events (tool use, etc.) are self-correcting and
# not worth persisting.
_DURABLE_EVENTS = {"Stop", "SessionEnd", "turn_ended", "agent-turn-complete", "task_complete"}
_ATTEMPTS = 3
_TIMEOUTS = (2.0, 3.0, 5.0)


def _post(url: str, secret: str, body: bytes) -> bool:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Mux-Hook-Secret": secret},
    )
    last_error: OSError | None = None
    for attempt in range(_ATTEMPTS):
        try:
            urllib.request.urlopen(request, timeout=_TIMEOUTS[attempt]).close()
            return True
        except OSError as error:
            last_error = error
            if attempt + 1 < _ATTEMPTS:
                time.sleep(0.2 * (attempt + 1))
    if last_error is not None:
        sys.stderr.write(f"swe-mux hook POST failed after {_ATTEMPTS} attempts: {last_error}\n")
    return False


def _spool(event: str, body: bytes) -> None:
    spool = os.environ.get("MUX_HOOK_SPOOL")
    if not spool:
        return
    try:
        with open(spool, "ab") as handle:
            handle.write(body + b"\n")
    except OSError as error:
        sys.stderr.write(f"swe-mux hook spool write failed for {event}: {error}\n")


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
    if not _post(url, secret, body) and event in _DURABLE_EVENTS:
        _spool(event, body)


if __name__ == "__main__":
    main()
