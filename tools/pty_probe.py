"""Attach to a session's PTY WebSocket, type a command, and report what came back.

Used by the Linux smoke test because terminal output has no HTTP endpoint: the
bytes only exist on the /pty/{sid} socket, which is the same path a browser uses.
Driving the real socket is also the point - it proves the POSIX pseudoterminal
reaches a client through the shipped transport, not merely that a process started.

The frame order is the daemon's, not this script's invention: `attach_ready` runs
the attach handshake (it decides delta versus window before any replay), then
`claim_input` takes input ownership, and only an owner's `input` frames are
written to the pty.
"""

from __future__ import annotations

import asyncio
import json
import sys

import aiohttp


async def main() -> int:
    port, sid, marker = sys.argv[1], sys.argv[2], sys.argv[3]
    url = f"http://127.0.0.1:{port}/pty/{sid}"
    seen = bytearray()
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, origin=f"http://127.0.0.1:{port}") as ws:
            await ws.send_str(json.dumps({"type": "attach_ready", "cols": 100, "rows": 30}))
            await ws.send_str(
                json.dumps({"type": "claim_input", "reason": "gesture", "device": "desktop"})
            )

            async def drain() -> None:
                sent = False
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.BINARY:
                        seen.extend(message.data)
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        frame = json.loads(message.data)
                        kind = frame.get("type")
                        if kind in {"output", "replay"} and isinstance(frame.get("data"), str):
                            seen.extend(frame["data"].encode("utf-8", "replace"))
                        if kind == "input_owner_granted" and not sent:
                            sent = True
                            await ws.send_str(
                                json.dumps({"type": "input", "data": f"echo {marker}\n"})
                            )
                    if not sent and b"$" in seen:
                        sent = True
                        await ws.send_str(json.dumps({"type": "input", "data": f"echo {marker}\n"}))
                    if seen.count(marker.encode()) >= 2:
                        return

            try:
                await asyncio.wait_for(drain(), timeout=20)
            except TimeoutError:
                pass
    text = bytes(seen).decode("utf-8", "replace")
    # Two occurrences: the shell echoing the keystrokes, and the command's own
    # output. One would only prove the write reached the pty, not that the child
    # ran and its stdout came back.
    if text.count(marker) >= 2:
        print("PTY-OK")
        return 0
    print("PTY-MISSING")
    print(repr(text[-2000:]))
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
