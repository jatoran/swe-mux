"""Tier `live_daemon`: a daemon that actually starts, serves a terminal, and stops.

Every other tier here tests the daemon's parts. Nothing tested the daemon.
`/api/health` names sixteen startup phases and not one of them was exercised end
to end anywhere; neither was shim writing, hook-settings materialization, a real
pseudoterminal spawn through the ordinary spawn route, a websocket attach, or a
process that has to exit without leaving children behind. The gap was measured
rather than assumed: an operator brought the daemon up on WSL Ubuntu for the
first time and hit four distinct failures inside an hour, none of which any of
the other 5400 tests could have caught.

**Deliberately shell-only.** The session half spawns a *shell*, never an agent, so
this tier needs no provider, no credential, and no quota, and can therefore run
on every CI runner - which is the entire point of a tier whose subject is "does
it start on this host". The agent-bearing tiers stay gated behind
`SWEMUX_RUN_LIVE_*_TESTS` and stay out of CI.

**In process and subprocess, because they answer different questions.** The
in-process daemon (`isolated_daemon`) is the only shape that can see the runtime
it built - the startup timeline, the adapters and the files they wrote, the pid a
pseudoterminal allocated - and it is fast enough to keep in a tier that runs on
every push. It exercises none of the real entry point: not the `muxd` console
script, not argv parsing, not `asyncio.run(serve(...))` binding a real socket,
and above all not a process that must exit and take its children with it, which
is the class of failure this tier was written for. So there is exactly one
subprocess smoke and it owns the lifecycle half.

**One xdist group.** These tests allocate real pseudoterminals and a real daemon
process, and `.worktree-verify` runs `--dist loadgroup`, so the mark keeps the
whole file on one worker rather than letting two real-console spawns and a
subprocess daemon contend inside one run. `test_conpty_integration.py` and
`test_pty_supervisor.py` carry the same mark for the same reason.

The guards that must run *without* this tier selected - marker-expression
agreement, and the self-check that the phase derivation finds anything at all -
live in `test_live_daemon_guards.py`, because a module-level mark would deselect
them from the gate they exist to protect.

**Known noise on a credentialed host, and why the separate CI step contains it.**
`_teardown_runtime` never closes `ProviderAccountManager`'s `aiohttp.ClientSession`,
which the `provider-accounts-reconcile` phase opens whenever this machine has a
live Claude login. Shutting the in-process daemon down therefore leaves it to a
finalizer, which prints "Unclosed client_session"/"Unclosed connector" at some
later, unrelated moment. It is a daemon defect rather than a harness one (the
`live_mcp` tier has always had it), it is invisible on a CI runner because there
is no credential to reconcile, and running this tier as its own pytest process
keeps the finalizer from reporting against a neighbouring test.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psutil
import pytest

from swe_mux import app_keys as keys
from swe_mux.launchers import agent_harnesses
from tests.support.live_daemon import (
    alive,
    assert_startup_is_complete,
    daemon_process,
    isolated_daemon,
)
from tests.support.settle import until

pytestmark = [pytest.mark.live_daemon, pytest.mark.xdist_group("live_daemon")]


# ------------------------------------------------------------------ startup phases


@pytest.mark.asyncio
async def test_the_daemon_reaches_ready_and_completes_every_phase_it_declares(
    tmp_path: Path,
) -> None:
    async with isolated_daemon(tmp_path) as daemon:
        assert_startup_is_complete(await daemon.health())


@pytest.mark.asyncio
async def test_starting_the_daemon_writes_the_agent_shims_and_hook_artifacts(
    tmp_path: Path,
) -> None:
    """The `adapters-and-shims` phase has to leave real files behind.

    A phase that completes proves it did not raise, not that it did anything.
    These are the artifacts an agent session cannot start without - the shim
    directory a shell pane's PATH is prefixed with, and the hook settings and MCP
    registration each instrumented adapter materializes at construction - and
    they are checked against the registry rather than by name, so a new harness
    joins this assertion without an edit.
    """
    async with isolated_daemon(tmp_path) as daemon:
        bin_dir = daemon.data_dir / "bin"
        assert bin_dir.is_dir(), f"no shim directory at {bin_dir}"
        written = {path.stem for path in bin_dir.iterdir()}
        assert written >= set(agent_harnesses()), (
            f"missing shims for {sorted(set(agent_harnesses()) - written)}"
        )

        declared = [
            (name, getattr(adapter, attribute, None))
            for name, adapter in daemon.app[keys.SESSIONS].adapters.items()
            for attribute in ("settings_path", "mcp_config_path")
        ]
        materialized = [(name, path) for name, path in declared if path is not None]
        assert materialized, "no adapter declared a settings or MCP config path at all"
        for name, path in materialized:
            assert Path(path).is_file(), f"{name} declared {path} and never wrote it"


# ----------------------------------------------------------------- session round trip


@pytest.mark.asyncio
async def test_a_shell_session_round_trips_through_a_real_pty_and_ends_clean(
    tmp_path: Path,
) -> None:
    """Register a Project, spawn a shell, type into it, read it back, end it.

    The marker is asserted **twice** in the output: once because the shell echoed
    the line as it was typed, once because the shell then ran it. The echo alone
    would pass against a pseudoterminal with nothing attached to it, so only the
    pair distinguishes a live shell from an open handle.

    Waiting on the bytes rather than on a clock is not a style preference: a cold
    PowerShell or bash under a loaded worker is nowhere near ready inside any
    window a fixed sleep would pick, and `until` returns the moment the output
    arrives on an idle one. The one wait that *is* timed guards an absence - the
    terminal going quiet - which is the shape load makes safer rather than
    riskier.
    """
    marker = f"muxlive{uuid.uuid4().hex[:8]}"
    async with isolated_daemon(tmp_path) as daemon:
        project_id = await daemon.register_project()
        spawned = await daemon.spawn(project_id, "shell", name="live-daemon-smoke")
        sid = str(spawned["id"])

        session = daemon.app[keys.SESSIONS].sessions[sid]
        child_pid = int(session.pty.pid)
        assert child_pid > 0, "the spawn allocated no process"
        assert psutil.pid_exists(child_pid), f"pid {child_pid} was never alive"

        async with daemon.attach_pty(sid) as terminal:
            await until(
                lambda: bool(terminal.output),
                seconds=90.0,
                what="the shell produced its first output",
            )
            # A fresh prompt, then quiet: input written while a shell is still
            # starting is dropped by the pseudoterminal and nothing reports it.
            await terminal.settle()
            await terminal.send_input("\r")
            await terminal.settle()

            await terminal.send_input(f"echo {marker}\r")
            await until(
                lambda: terminal.text.count(marker) >= 2,
                seconds=90.0,
                what=f"the shell echoed and then ran `echo {marker}`; saw {terminal.text[-400:]!r}",
            )

        assert (await daemon.session(sid)) is not None
        await daemon.end_session(sid)
        assert (await daemon.session(sid)) is None
        await until(
            lambda: not alive(child_pid),
            seconds=60.0,
            what=f"the shell process {child_pid} was reaped by ending its session",
        )
        # Health is the daemon's own account of itself, and it must agree.
        after = await daemon.health()
        assert after["live_sessions"] == 0, after
        assert_startup_is_complete(after)


# ---------------------------------------------------------------------- lifecycle


@pytest.mark.asyncio
async def test_the_muxd_entry_point_starts_serves_and_stops_without_orphans(
    tmp_path: Path,
) -> None:
    """The one smoke over the real console script, argv, socket, and exit.

    Everything here is invisible to the in-process harness: `muxd` is resolved
    and executed as an installed entry point, `--config` and `--local-only` go
    through `argparse`, `serve()` binds an actual TCP listener and prints the
    line it prints in production, and the process then has to unwind through
    `runner.cleanup()` and `_teardown_runtime` and exit zero.

    `crash.log` is asserted *empty* rather than absent on purpose:
    `enable_crash_tracebacks` opens it for append during startup, so the file
    always exists and its emptiness is the signal. A faulthandler dump in it is
    a native crash that an exit code of zero would not have shown.
    """
    async with daemon_process(tmp_path) as daemon:
        assert_startup_is_complete(await daemon.wait_ready())

        # The daemon reports through a socket it bound itself rather than through
        # a test server, so its own listener line is evidence the real path ran.
        listening = f"Running on http://127.0.0.1:{daemon.port}"
        assert listening in daemon.stdout.decode("utf-8", "replace"), daemon.diagnostics()

        descendants = [child.pid for child in daemon.descendants()]

        assert await daemon.request_shutdown() == 202, daemon.diagnostics()
        returncode = await daemon.wait_for_exit()
        assert returncode == 0, f"muxd exited {returncode}\n{daemon.diagnostics()}"

        assert not alive(daemon.pid), "the daemon process outlived its own shutdown"
        survivors = [pid for pid in descendants if alive(pid)]
        assert not survivors, f"orphaned children survived shutdown: {survivors}"

        crash_log = daemon.data_dir / "crash.log"
        assert crash_log.is_file(), (
            "crash.log was never opened, so startup did not reach "
            f"enable_crash_tracebacks\n{daemon.diagnostics()}"
        )
        assert crash_log.stat().st_size == 0, crash_log.read_text(encoding="utf-8")

        # It used the data directory it was pointed at, which is under tmp_path
        # and therefore never the operator's.
        assert (daemon.data_dir / "daemon.log").is_file()
        assert daemon.data_dir.is_relative_to(tmp_path)
