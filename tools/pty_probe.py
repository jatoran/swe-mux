"""Drive a session's PTY WebSocket - the same socket a browser uses.

Terminal output has no HTTP endpoint; the bytes only exist on `/pty/{sid}`. Using
the real socket is the point: it proves the pseudoterminal reaches a client
through the shipped transport, not merely that a process started.

The frame order is the daemon's, not this script's invention. `attach_ready` runs
the attach handshake (it decides delta versus window before any replay), then
`claim_input` takes input ownership, and only an owner's `input` frames are
written to the pty.

Two modes:

    pty_probe.py PORT SID MARKER               echo MARKER, expect it back twice
    pty_probe.py PORT SID "" --agent-prompt    send a short prompt to an agent TUI

The echo mode wants two occurrences deliberately: one would only prove the write
reached the pty, not that the child ran and its stdout came back.
"""

from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

AGENT_PROMPT = "Reply with exactly: MUX-LINUX-AGENT-OK"
ENTER = "\r"
# The text and the carriage return are two writes with a pause between them, not
# one. An agent composer commits typed text asynchronously, so a CR arriving in the
# same write is processed against a composer that is still empty and the prompt is
# swallowed - the session just sits there, which reads as an agent that ignored
# you. swe-mux's own send path waits for the same reason (design/features/voice.md:
# "waits 180 ms for bracketed-paste commit").
COMPOSER_COMMIT_SECONDS = 0.4

# An agent TUI asks the terminal where the cursor is (DSR, ESC[6n) before it paints
# anything, and blocks until something answers. A browser answers because xterm.js
# *is* a terminal; this script is not, so without a reply the CLI sits at a cursor
# query forever. On the measured run that looked exactly like a hung agent and also
# burned a core busy-waiting - which is the whole reason an agent cannot be driven
# over this socket by a client that only reads.
DSR_QUERY = b"\x1b[6n"
DSR_REPLY = "\x1b[1;1R"
# Some TUIs also probe for device attributes before drawing.
DA_QUERY = b"\x1b[c"
DA_REPLY = "\x1b[?1;2c"

# Enough painted bytes to believe a TUI has drawn a frame rather than just its
# first escape sequence.
PAINTED_BYTES = 2000
# How long the screen must stay painted before typing into it. A prompt sent into a
# half-drawn composer is swallowed, which reads as an agent that never replied.
SETTLE_SECONDS = 4.0


def _input_frame(data: str, *, kind: str | None = None) -> str:
    """One `input` frame.

    A helper because these carry control characters: an escape written inline is
    easy to mangle in a later source edit, and a literal CR in the source is
    invisible in review.
    """
    frame: dict[str, str] = {"type": "input", "data": data}
    if kind:
        frame["kind"] = kind
    return json.dumps(frame)


async def run(port: str, sid: str, marker: str, *, agent: bool) -> int:
    url = f"http://127.0.0.1:{port}/pty/{sid}"
    seen = bytearray()
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, origin=f"http://127.0.0.1:{port}") as ws:
            await ws.send_str(json.dumps({"type": "attach_ready", "cols": 100, "rows": 30}))
            await ws.send_str(
                json.dumps({"type": "claim_input", "reason": "gesture", "device": "desktop"})
            )

            answered = {"dsr": 0, "da": 0}
            trusted = {"done": False}

            async def answer_terminal_queries(chunk: bytes) -> None:
                """Reply to the terminal probes a TUI blocks on."""
                if DSR_QUERY in chunk and answered["dsr"] < 8:
                    answered["dsr"] += 1
                    await ws.send_str(_input_frame(DSR_REPLY, kind="terminal_response"))
                if DA_QUERY in chunk and answered["da"] < 4:
                    answered["da"] += 1
                    await ws.send_str(_input_frame(DA_REPLY, kind="terminal_response"))

            async def drain() -> None:
                sent = False
                painted_at: float | None = None
                loop = asyncio.get_running_loop()
                async for message in ws:
                    chunk = b""
                    if message.type == aiohttp.WSMsgType.BINARY:
                        chunk = bytes(message.data)
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        frame = json.loads(message.data)
                        if frame.get("type") in {"output", "replay"} and isinstance(
                            frame.get("data"), str
                        ):
                            chunk = frame["data"].encode("utf-8", "replace")
                    if chunk:
                        seen.extend(chunk)
                        await answer_terminal_queries(chunk)

                    if not agent:
                        if not sent and b"$" in seen:
                            sent = True
                            await ws.send_str(_input_frame(f"echo {marker}\n"))
                        if seen.count(marker.encode()) >= 2:
                            return
                        continue

                    # A first run in a folder the CLI has not seen opens a trust
                    # dialog, and nothing else happens until it is answered.
                    # Accepting the highlighted default is what a human does; the
                    # folder is the checkout the caller pointed this script at.
                    if b"trust this folder" in seen and not trusted["done"]:
                        trusted["done"] = True
                        await ws.send_str(_input_frame(ENTER))
                        seen.clear()
                        painted_at = None
                        continue
                    if not sent and len(seen) > PAINTED_BYTES:
                        painted_at = painted_at or loop.time()
                        if loop.time() - painted_at > SETTLE_SECONDS:
                            sent = True
                            await ws.send_str(_input_frame(AGENT_PROMPT))
                            await asyncio.sleep(COMPOSER_COMMIT_SECONDS)
                            await ws.send_str(_input_frame(ENTER))
                    if b"MUX-LINUX-AGENT-OK" in seen:
                        return

            try:
                await asyncio.wait_for(drain(), timeout=240 if agent else 20)
            except TimeoutError:
                pass
    text = bytes(seen).decode("utf-8", "replace")
    if agent:
        ok = "MUX-LINUX-AGENT-OK" in text
        print("AGENT-REPLIED" if ok else "AGENT-NO-REPLY")
    else:
        ok = text.count(marker) >= 2
        print("PTY-OK" if ok else "PTY-MISSING")
    if not ok:
        print(repr(text[-2000:]))
    return 0 if ok else 1


def main() -> int:
    port, sid, marker = sys.argv[1], sys.argv[2], sys.argv[3]
    agent = "--agent-prompt" in sys.argv[4:]
    return asyncio.run(run(port, sid, marker, agent=agent))


if __name__ == "__main__":
    raise SystemExit(main())
