"""The POSIX stand-in for kill-on-job-close: a small process that outlives the daemon.

`posix_process_group` can address a session's whole tree and end it in an orderly
way, which covers every case where the daemon is alive to ask. It cannot cover the
case the Windows Job object covers for free: the daemon dying *without* asking.
A Job handle is closed by the kernel when its owner dies however it dies - crash,
SIGKILL, power-off of the process - and `KILL_ON_JOB_CLOSE` ends the tree with no
surviving code of ours involved. Nothing on POSIX does that.

So the guardian is that surviving code. One per session, started outside the group
it watches, holding the read end of a pipe whose write end the daemon keeps open:

    daemon ──write end──┐
                        │ pipe
    guardian ──read end─┘   →  EOF means the daemon is gone  →  kill the group

EOF on that pipe is the signal, and it is the right signal because the kernel
delivers it unconditionally. A crashed daemon cannot decline to close its file
descriptors, so unlike a heartbeat there is no failure mode where the daemon dies
and the guardian keeps waiting. A clean shutdown writes ``release`` first, which
tells the guardian to exit *without* killing - that is how a session survives a
deliberate daemon restart, the property the whole supervisor split exists to
provide.

The guardian is deliberately tiny and depends on nothing but the standard library:
it has to keep working while the rest of the process is gone.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import IO

log = logging.getLogger(__name__)

# Written by the daemon to end the watch without killing anything.
RELEASE_COMMAND = b"release\n"
# Written to hand the guardian one more group to watch, so a nested per-session
# reaper does not need a second process.
WATCH_PREFIX = b"watch "


class PosixGuardian:
    """A daemon-side handle on one guardian process."""

    def __init__(self, process: subprocess.Popen[bytes], pgid: int) -> None:
        self._process = process
        self._pgid = pgid
        self._released = False

    @property
    def pid(self) -> int:
        return int(self._process.pid)

    @property
    def pgid(self) -> int:
        return self._pgid

    def watch(self, pgid: int) -> None:
        """Add another process group to this guardian's watch set."""
        self._send(WATCH_PREFIX + str(pgid).encode("ascii") + b"\n")

    def release(self) -> None:
        """Stop guarding without killing anything. Used for a deliberate restart."""
        if self._released:
            return
        self._released = True
        self._send(RELEASE_COMMAND)
        self._close()

    def close(self) -> None:
        """Drop the pipe so the guardian kills the group. Used for a real teardown."""
        self._close()

    def _send(self, payload: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.closed:
            return
        try:
            stdin.write(payload)
            stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            log.debug("could not talk to the session guardian: %s", exc)

    def _close(self) -> None:
        stdin = self._process.stdin
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except OSError:
                pass


def start_guardian(pgid: int) -> PosixGuardian | None:
    """Start a guardian watching ``pgid``. Returns None when one cannot be started.

    A missing guardian is a degraded session, not a failed one: the group is still
    addressable and an orderly shutdown still ends it. Only an *unclean* daemon
    death would then leak the tree, which is exactly the shipped behaviour on a
    host with no guardian at all, so refusing to spawn the session over it would
    trade a rare leak for a certain outage.
    """
    if os.name == "nt":  # pragma: no cover - the Job object owns this on Windows
        return None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "swe_mux.posix_guardian", str(pgid)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own session, so it is not inside the group it is watching and
            # does not die with it - and so a terminal signal aimed at the daemon
            # does not reach it either.
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError) as exc:
        log.warning("could not start a session guardian for group %s: %s", pgid, exc)
        return None
    return PosixGuardian(process, pgid)


def _guard(stream: IO[bytes], pgids: set[int]) -> bool:
    """Read commands until EOF. True when released, False when the daemon vanished."""
    # unsupervised-loop-ok: runs in the guardian process, not the daemon. There is
    # no daemon event loop here to supervise it from, and blocking forever on this
    # read is the entire job - the loop ends when the pipe does.
    while True:
        line = stream.readline()
        if not line:
            return False
        command = line.strip()
        if command == RELEASE_COMMAND.strip():
            return True
        if command.startswith(WATCH_PREFIX.strip()):
            _, _, raw = command.partition(b" ")
            try:
                pgids.add(int(raw))
            except ValueError:
                continue


def main(argv: list[str] | None = None) -> int:
    """Guardian entry point. Runs in its own process with no daemon state."""
    args = list(sys.argv[1:] if argv is None else argv)
    pgids: set[int] = set()
    for raw in args:
        try:
            pgids.add(int(raw))
        except ValueError:
            return 2
    if not pgids:
        return 2
    released = _guard(sys.stdin.buffer, pgids)
    if released:
        return 0
    # The daemon is gone and did not release us. End every watched group the way
    # an orderly stop would: signal, bounded wait, then kill.
    from .posix_process_group import terminate_group

    for pgid in sorted(pgids):
        terminate_group(pgid)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
