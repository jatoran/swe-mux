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
    "SessionStart",
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


# The hook credentials this process must use, named by `--identity <path>` in the
# command the harness settings file carries. It exists because the environment is
# not a trustworthy channel for them.
#
# Measured 2026-08-10: Claude Code 2.1.227 parks a pane's conversation into a
# background job hosted by a shared, long-lived `claude daemon run` process. That
# daemon is spawned once per machine by whichever CLI happened to need it first,
# and every background agent it later starts inherits *that* process's
# environment — including `MUX_HOOK_URL`/`MUX_HOOK_SECRET` belonging to a pane
# that has since exited. One such daemon posted 744 hook events to
# `/api/hooks/<retired session>` and got 744 HTTP 404s while the pane the work
# actually belonged to received exactly one hook in its lifetime.
#
# `--settings` is propagated per request and always names the requesting pane's
# file, so the settings file is the one channel that stays correct across that
# hand-off. The identity file therefore wins over the environment; the
# environment remains the fallback for harnesses with no per-session settings
# file of their own.
_IDENTITY_FLAG = "--identity"


def _identity(path: str) -> dict[str, str]:
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8", errors="replace"))
    except (OSError, ValueError) as error:
        sys.stderr.write(f"swe-mux hook identity {path} unreadable: {error}\n")
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, str) and value}


def _parse_argv(argv: list[str]) -> tuple[str, dict[str, str], str]:
    """Split ``<event> [--identity PATH] [inline payload]``.

    The inline payload is the legacy fallback for a harness that passes the hook
    body as an argument instead of on stdin, so the flag has to be consumed
    before the first remaining positional is read as that body.
    """
    event = argv[1] if len(argv) > 1 else "hook"
    identity: dict[str, str] = {}
    positional: list[str] = []
    rest = argv[2:]
    index = 0
    while index < len(rest):
        if rest[index] == _IDENTITY_FLAG and index + 1 < len(rest):
            identity = _identity(rest[index + 1])
            index += 2
            continue
        positional.append(rest[index])
        index += 1
    return event, identity, positional[0] if positional else ""


def _spool(spool: str | None, event: str, payload: object) -> None:
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
    event, identity, inline_payload = _parse_argv(sys.argv)
    url = identity.get("url") or os.environ.get("MUX_HOOK_URL")
    secret = identity.get("secret") or os.environ.get("MUX_HOOK_SECRET")
    spool = identity.get("spool") or os.environ.get("MUX_HOOK_SPOOL")
    if not url or not secret:
        return
    raw = _read_payload() or inline_payload
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}
    if event == "codex_notify" and isinstance(payload, dict) and payload.get("type"):
        event = str(payload["type"])
    body = json.dumps({"event": event, "payload": payload}).encode()
    if not _post(url, secret, body) and event in _DURABLE_EVENTS:
        _spool(spool, event, payload)


if __name__ == "__main__":
    main()
