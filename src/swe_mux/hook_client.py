from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

# Events whose loss leaves a session misrepresented until something else happens
# to correct it; when the POST cannot be delivered they are spooled to disk for
# the daemon to replay. Tool-use events are self-correcting and not persisted.
#
# Blocking events are here for the same reason terminal ones are: a permission
# dialog raised during a session-preserving daemon restart (a routine operation)
# has no second source. The transcript tail reads "open" (an in-flight tool) and
# the PTY reads "approval", so neither watchdog path can fire and the session
# sits displayed as "working" until the 900s no-evidence fleet alarm — with the
# user's attention on a prompt nothing is showing them.
_DURABLE_EVENTS = {
    "Stop",
    "SessionEnd",
    "turn_ended",
    "agent-turn-complete",
    "task_complete",
    "PermissionRequest",
    "Notification",
    "approval_needed",
    "exec_approval_request",
    "apply_patch_approval_request",
    "request_user_input",
}
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


def _spool(event: str, payload: object) -> None:
    spool = os.environ.get("MUX_HOOK_SPOOL")
    if not spool:
        return
    # The wall-clock stamp is what lets the daemon tell a genuinely-missed
    # terminal event from a stale one: a Stop spooled during turn N must not be
    # replayed into turn N+1, and a spool left behind by a demoted shell must not
    # replay into the next promotion.
    record = json.dumps({"event": event, "payload": payload, "spooled_at": time.time()})
    try:
        with open(spool, "ab") as handle:
            handle.write(record.encode() + b"\n")
    except OSError as error:
        sys.stderr.write(f"swe-mux hook spool write failed for {event}: {error}\n")


def _read_payload() -> str:
    """The hook payload, decoded as the UTF-8 that JSON is defined to be.

    `sys.stdin.read()` decodes with the process's locale encoding, which on
    Windows is the ANSI code page (cp1252) with `errors="surrogateescape"`. Claude
    writes hook JSON as UTF-8, so every non-ASCII character arrived mojibaked —
    `⚠️` (`E2 9A A0 EF B8 8F`) became `â` `š` `\\xa0` `ï` `¸` plus a *lone
    surrogate* `\\udc8f`, because 0x8F is one of the five bytes cp1252 leaves
    undefined and surrogateescape is where undecodable bytes go.

    The mojibake alone was silent corruption of every prompt, title, and history
    entry containing an emoji or an accent. The lone surrogate was worse: it is
    not encodable UTF-8 at all, so the first thing downstream to call `.encode()`
    raised, and for the titler that meant panes opened from a phone (where the
    pasted text carries emoji) never got a name.

    Reading bytes and decoding explicitly is the fix. `errors="replace"` covers
    genuinely malformed input without ever reintroducing a lone surrogate.
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        raw: bytes = buffer.read()
        return raw.decode("utf-8", errors="replace")
    # No binary view: a frozen windowed build can hand us a stub stream. Nothing
    # better is available there, and it is not the path hooks actually take.
    return str(sys.stdin.read()) if sys.stdin is not None else ""


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "hook"
    url = os.environ.get("MUX_HOOK_URL")
    secret = os.environ.get("MUX_HOOK_SECRET")
    if not url or not secret:
        return
    raw = _read_payload() or (sys.argv[2] if len(sys.argv) > 2 else "")
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}
    if event == "codex_notify" and isinstance(payload, dict) and payload.get("type"):
        event = str(payload["type"])
    body = json.dumps({"event": event, "payload": payload}).encode()
    if not _post(url, secret, body) and event in _DURABLE_EVENTS:
        _spool(event, payload)


if __name__ == "__main__":
    main()
