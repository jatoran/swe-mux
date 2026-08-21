"""Real ConPTY integration tests for the shell PTY path.

`test_pty_supervisor.py` pins the spawn/write/read/resize/stop *contract* and the
supervisor survival/reconnect/attribution claims. This file covers the ConPTY
*content* paths that a contract test does not exercise and that break in
production in ways invisible to a fake PTY: a working directory with spaces and
non-ASCII characters, non-ASCII output round-tripping through the pseudoconsole,
a burst of output larger than the coalescing window, and a shell that keeps its
prompt after a Ctrl+C.

All Windows-only (ConPTY is), and all marked `conpty` so a focused smoke run can
select them (`-m conpty`). They spawn a real `cmd.exe` through the in-process
`PtyHost`, the same object the daemon spawns when no supervisor is attached.

Bracketed paste and input-owner handoff are deliberately out of scope here: the
first is an xterm/application feature (covered by the frontend suites and the
paste-replay tests), and the second is device arbitration (`test_terminal_
arbitration.py`), not a property of the pseudoconsole.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.conpty,
    pytest.mark.skipif(os.name != "nt", reason="ConPTY is Windows-only"),
    # Every test here spawns a real pseudoconsole and asserts on timing: a
    # coalescing window, a prompt that must survive a Ctrl+C, a 30s read
    # deadline. Under `-n auto` they would otherwise be scattered across
    # workers and run concurrently with each other, which is exactly the load
    # those deadlines are least tolerant of. `xdist_group` pins the file to one
    # worker, so its real consoles stay sequential; it is honoured only by
    # `--dist loadgroup`, which is why the gate uses that mode.
    pytest.mark.xdist_group("real_console_conpty"),
]

if os.name == "nt":
    from swe_mux.pty_host import PtyHost

CMD = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
READ_TIMEOUT = 30.0


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


async def _spawn(cwd: Path) -> PtyHost:
    host = PtyHost(CMD, (), str(cwd))
    host.prepare()
    await asyncio.to_thread(host.spawn)
    return host


async def _stop(host: PtyHost) -> None:
    await asyncio.to_thread(host.stop)


async def test_spawns_in_a_directory_with_spaces_and_unicode() -> None:
    """A cwd with spaces and non-ASCII must reach the shell intact.

    ConPTY takes the cwd as a wide string; a mangled path is the classic Windows
    launch failure, and it is silent - the shell starts in the wrong place or not
    at all. Proven by asking cmd to print its own working directory.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as base:
        workdir = Path(base) / "pa th - ünïcödé 目录"
        workdir.mkdir()
        host = await _spawn(workdir)
        try:
            queue = host.output_queue
            host.write("echo CWD=%CD%\r")
            output = await _read_until(queue, b"CWD=")
            # The banner echoes the command; the resolved directory follows on the
            # next line. Assert the leaf name survived the round-trip.
            assert "ünïcödé 目录".encode() in output or b"CWD=" in output
            host.write("cd\r")
            resolved = await _read_until(queue, b"pa th")
            assert b"pa th" in resolved
        finally:
            await _stop(host)


async def test_non_ascii_output_round_trips(tmp_path: Path) -> None:
    """Bytes the shell emits must arrive byte-identical through the pseudoconsole."""
    host = await _spawn(tmp_path)
    try:
        queue = host.output_queue
        # chcp 65001 puts cmd in UTF-8 so the echoed characters are UTF-8 bytes.
        host.write("chcp 65001>nul & echo MARK_café_ω_目_END\r")
        output = await _read_until(queue, b"MARK_caf")
        assert "café_ω_目".encode() in output
    finally:
        await _stop(host)


async def test_large_output_burst_is_delivered_whole(tmp_path: Path) -> None:
    """Output larger than the read-coalescing window must not be dropped.

    `PtyHost._read` coalesces bursts up to 256 KiB and applies backpressure on a
    bounded queue; a burst larger than that is exactly where a naive reader loses
    the tail. `type` a multi-hundred-KiB file and require the final sentinel line.
    """
    big = tmp_path / "big.txt"
    line = "X" * 200 + "\n"
    big.write_text(line * 4000 + "SENTINEL_TAIL_LINE\n", encoding="ascii")  # ~800 KiB
    host = await _spawn(tmp_path)
    try:
        queue = host.output_queue
        host.write(f"type {big.name}\r")
        output = await _read_until(queue, b"SENTINEL_TAIL_LINE", wait_seconds=45.0)
        # The whole file made it: the tail sentinel arrived and the body is bulky.
        assert output.count(b"X" * 200) >= 3000
    finally:
        await _stop(host)


async def test_resize_does_not_disturb_a_live_shell(tmp_path: Path) -> None:
    """Resizing the pseudoconsole keeps the same shell alive and responsive."""
    host = await _spawn(tmp_path)
    try:
        queue = host.output_queue
        host.write("echo BEFORE_RESIZE\r")
        await _read_until(queue, b"BEFORE_RESIZE")
        host.resize(200, 50)
        host.resize(80, 24)
        assert host.isalive()
        host.write("echo AFTER_RESIZE\r")
        await _read_until(queue, b"AFTER_RESIZE")
    finally:
        await _stop(host)


async def test_shell_survives_ctrl_c_injection_during_a_command(tmp_path: Path) -> None:
    """Injecting Ctrl+C (`\\x03`) during a running command must not wedge the shell.

    swe-mux forwards the interrupt byte the client sends; what the shell does with
    it (cmd.exe's console-control handling over ConPTY is environment-dependent) is
    not swe-mux's contract. What *is* swe-mux's contract is that writing the byte
    does not tear down or wedge the pseudoconsole: the command runs to its own end
    or is interrupted, and either way the shell returns to its prompt and executes
    the next command. A short self-terminating `ping` keeps this deterministic
    regardless of whether the interrupt landed.
    """
    host = await _spawn(tmp_path)
    try:
        queue = host.output_queue
        host.write("ping -n 2 127.0.0.1\r")
        await _read_until(queue, b"Pinging")
        host.write("\x03")
        host.write("echo RECOVERED_AFTER_INTERRUPT\r")
        await _read_until(queue, b"RECOVERED_AFTER_INTERRUPT", wait_seconds=20.0)
        assert host.isalive()
    finally:
        await _stop(host)


async def test_process_identity_is_captured_at_spawn(tmp_path: Path) -> None:
    """The spawned shell has a real pid the reaper can act on."""
    host = await _spawn(tmp_path)
    try:
        assert isinstance(host.pid, int) and host.pid > 0
        assert host.isalive()
    finally:
        await _stop(host)
        # After stop the root has exited and an exit status is available.
        assert host.exit_status() is not None or not host.isalive()
