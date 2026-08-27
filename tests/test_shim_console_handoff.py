"""The mux agent shim, under a real pseudoconsole, holding the console for its child.

Two properties, both about the wrapper staying out of the way for exactly as long
as its child runs:

1. The chain returns only when the agent does, so the shell keeps its prompt
   suppressed for the agent's whole life. This is the invariant the whole shim
   launch depends on, and nothing asserted it before.
2. A console interrupt does not unblock the wrapper. A control event is delivered
   to *every* process attached to a console rather than to a foreground process
   group, so an unprotected wrapper exits on the same Ctrl+C the agent merely
   clears its composer on.

Real ConPTY because a fake PTY, a pipe, or a plain `subprocess` call all reproduce
the healthy behaviour and prove nothing about (2).

**(2) is honest about not always running.** Measured 2026-08-27, injecting `0x03`
through mux's own pseudoconsole does not produce a control event at all -
winpty/OpenConsole headless delivers it as a key event - so on such a host there is
no interrupt to hold and the test skips with that reason rather than passing
vacuously. The fake agent installs its own handler and the test reads *its* verdict
to decide, so this can never be a silent green.
Consequently the interrupt is not how the 2026-08-27 incident happened; the cause
of that one is still open, and `agent_launcher`'s lifecycle reports exist to answer
it (`console_contention.py`).

Windows-only, and pinned to its own xdist group for the reason the other real-console
file is: these spawn real pseudoconsoles and assert on timing, and running two of
them concurrently on one worker is the load their deadlines tolerate least.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.conpty,
    pytest.mark.skipif(os.name != "nt", reason="ConPTY is Windows-only"),
    pytest.mark.xdist_group("real_console_shim"),
]

if os.name == "nt":
    from swe_mux.pty_host import PtyHost

CMD = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
READ_TIMEOUT = 40.0

# A stand-in for the real CLI. It reports the pid of whatever launched it (the
# wrapper), notes whether a console control event reached it, and stays alive long
# enough for the test to look at the wrapper afterwards. It is deliberately a
# console application that *survives* Ctrl+C, because that is what the agents mux
# launches do - Claude clears its composer and keeps running - and it is exactly
# that asymmetry (agent survives, wrapper does not) that produced the incident.
FAKE_AGENT = '''
import ctypes, json, os, sys, threading, time
from ctypes import wintypes

state = {"ppid": os.getppid(), "pid": os.getpid(), "interrupted": False}
report = sys.argv[1]

def write():
    tmp = report + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, report)

@ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
def handler(event):
    if event in (0, 1):
        state["interrupted"] = True
        write()
        return True
    return False

ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)

# A real agent CLI holds a read on the console, and that has to be modelled here:
# Windows turns an injected 0x03 into a CTRL_C_EVENT while console input is being
# *read*, so an agent that only slept would leave the byte sitting in the input
# buffer and no interrupt would ever be delivered to anyone.
def drain():
    while True:
        try:
            if not os.read(0, 1):
                return
        except OSError:
            return

threading.Thread(target=drain, daemon=True).start()
write()
sys.stdout.write("FAKE_AGENT_READY\\r\\n")
sys.stdout.flush()
deadline = time.time() + 12.0
while time.time() < deadline:
    time.sleep(0.05)
state["finished"] = True
write()
sys.stdout.write("FAKE_AGENT_FINISHED\\r\\n")
sys.stdout.flush()
'''


async def _read_until(
    queue: asyncio.Queue[bytes], needle: bytes, *, wait_seconds: float = READ_TIMEOUT
) -> bytes:
    buffer = b""
    deadline = time.monotonic() + wait_seconds
    while needle not in buffer:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {needle!r}; got {buffer[-400:]!r}")
        chunk = await asyncio.wait_for(queue.get(), timeout=remaining)
        if chunk == b"":
            raise AssertionError(f"pty ended before {needle!r}; got {buffer[-400:]!r}")
        buffer += chunk
    return buffer


def _pid_alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).is_running()
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True


async def _stop(host: PtyHost) -> None:
    await asyncio.to_thread(host.stop)


async def test_shim_holds_the_console_through_ctrl_c(tmp_path: Path) -> None:
    """A console interrupt must not unblock the wrapper while its agent still runs.

    The shape it guards: an unprotected wrapper takes `KeyboardInterrupt` out of its
    wait and exits, leaving the shell printing a prompt over a live agent that had
    merely cleared its composer. Both then read the same pseudoconsole.

    Skips where the pseudoconsole does not deliver control events at all, which is
    the measured behaviour of the one mux itself allocates; see the module docstring.
    """
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT, encoding="utf-8")
    report = tmp_path / "agent.json"

    host = PtyHost(
        CMD,
        (),
        str(tmp_path),
        env_extra={
            "MUX_CLAUDE_EXE": sys.executable,
            "MUX_CLAUDE_ARGS": json.dumps([str(agent), str(report)]),
            # Nothing to promote, demote, or report to: this test is about the
            # console, and an unset URL makes every notification a no-op.
            "MUX_PROMOTE_URL": "",
            "MUX_DEMOTE_URL": "",
            "MUX_SHIM_URL": "",
            "MUX_CLAUDE_SETTINGS": "",
            "MUX_CLAUDE_MCP_CONFIG": "",
        },
    )
    host.prepare()
    await asyncio.to_thread(host.spawn)
    try:
        queue = host.output_queue
        host.write(f'"{sys.executable}" -m swe_mux.agent_launcher claude\r')
        await _read_until(queue, b"FAKE_AGENT_READY")

        state = json.loads(report.read_text(encoding="utf-8"))
        wrapper_pid = int(state["ppid"])
        assert _pid_alive(wrapper_pid), "the wrapper should be alive while its child runs"

        host.write("\x03")
        # Give the event time to be delivered and acted on. A negative assertion
        # ("the wrapper did not exit") is exactly the case a fixed wait is safe
        # for: load can only make it safer.
        await asyncio.sleep(2.0)

        state = json.loads(report.read_text(encoding="utf-8"))
        if not state.get("interrupted"):
            pytest.skip(
                "Ctrl+C did not cross this pseudoconsole, so there is nothing to hold; "
                "cmd.exe's console-control behaviour over ConPTY is environment-dependent"
            )

        assert _pid_alive(wrapper_pid), (
            "the shim exited while its agent was still running: the shell now has the "
            "console back and two processes are reading it"
        )
        # And it really does keep waiting, rather than merely being slow to die.
        await _read_until(queue, b"FAKE_AGENT_FINISHED", wait_seconds=30.0)
    finally:
        await _stop(host)


async def test_shim_waits_for_its_child_without_an_interrupt(tmp_path: Path) -> None:
    """The plain case, as the floor the interrupt test is measured against.

    Without this, a shim that unblocks *always* would make the test above pass by
    never having anything to hold.
    """
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT.replace("deadline = time.time() + 12.0", "deadline = 0"), "utf-8")
    report = tmp_path / "agent.json"

    host = PtyHost(
        CMD,
        (),
        str(tmp_path),
        env_extra={
            "MUX_CLAUDE_EXE": sys.executable,
            "MUX_CLAUDE_ARGS": json.dumps([str(agent), str(report)]),
            "MUX_PROMOTE_URL": "",
            "MUX_DEMOTE_URL": "",
            "MUX_SHIM_URL": "",
            "MUX_CLAUDE_SETTINGS": "",
            "MUX_CLAUDE_MCP_CONFIG": "",
        },
    )
    host.prepare()
    await asyncio.to_thread(host.spawn)
    try:
        queue = host.output_queue
        host.write(f'"{sys.executable}" -m swe_mux.agent_launcher claude\r')
        await _read_until(queue, b"FAKE_AGENT_FINISHED")
        # The shell only gets its prompt back after the whole chain has returned,
        # which is the property the interrupt must not break.
        host.write("echo SHIM_CHAIN_RETURNED\r")
        await _read_until(queue, b"SHIM_CHAIN_RETURNED", wait_seconds=20.0)
        assert json.loads(report.read_text(encoding="utf-8"))["finished"] is True
    finally:
        await _stop(host)
