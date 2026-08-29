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
is the class of failure this tier was written for. So the subprocess shape owns
the lifecycle half.

**The subprocess half runs at the shipped default, and since 2026-08-28 that
means supervised.** It used to pin `pty_supervisor_enabled = false`, which was
the same configuration as the default and stopped being so the day the default
moved; a tier whose subject is "does the real thing work on this host" must not
quietly test a configuration nobody has. Two tests follow from that. The first
proves a default install reaches a real supervisor process and that a `quit`
still takes every child with it - the supervisor named by pid rather than
excused as "the one that was supposed to survive". The second proves the claim
the default was moved for: a shell spawned through the supervisor outlives its
daemon, is adopted by a successor, and is still the *same child*, which only its
own replayed output can establish.

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

import asyncio
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path

import psutil
import pytest

from swe_mux import app_keys as keys
from swe_mux.launchers import agent_harnesses
from tests.support.live_daemon import (
    alive,
    assert_startup_is_complete,
    daemon_process,
    daemon_spec,
    isolated_daemon,
    supervisor_discovery,
    supervisor_reaped,
)
from tests.support.settle import until

pytestmark = [pytest.mark.live_daemon, pytest.mark.xdist_group("live_daemon")]


async def _assert_reaped(
    pids: Sequence[int], *, supervisor_pid: int | None, what: str, seconds: float = 60.0
) -> None:
    """Every pid in `pids` is gone, with any survivor named by its role.

    Bounded rather than instantaneous because a reap is a request: the daemon
    asks the supervisor to close its Job and stop, and the acknowledgement
    precedes the exit. The bound is what keeps this an assertion and not a hope,
    and naming the supervisor in the failure is what keeps a real regression -
    "quit no longer stops the supervisor" - from reading as an anonymous pid.
    """

    def gone() -> bool:
        return not [pid for pid in pids if alive(pid)]

    try:
        await until(gone, seconds=seconds, what=what)
    except AssertionError:
        survivors = [pid for pid in pids if alive(pid)]
        named = [
            f"{pid} (the PTY supervisor)" if pid == supervisor_pid else str(pid)
            for pid in survivors
        ]
        raise AssertionError(f"{what}; these survived: {', '.join(named)}") from None


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

    **Run at the shipped default, which since 2026-08-28 means the supervisor is
    on.** That changes what this test proves in both directions. It now also
    proves the default *works* on this host - a real second process, started
    from `python -m swe_mux.supervisor` or a dedicated bundle, reached over a
    loopback socket and a token handshake - which is the foundation of the claim
    that sessions outlive a daemon restart, and which had never been exercised
    anywhere but the operator's own machine.

    And the orphan assertion is *stronger* than it was, not weaker. The obvious
    way to accommodate a supervisor here is to let some survivor through, and
    that is exactly the reasoning this test exists to refuse: "the survivor is
    the one that was supposed to survive" is unfalsifiable when it is inferred
    from what happens to be left running. So the supervisor is identified by pid,
    from the daemon, *before* the shutdown - and then held to the same standard
    as everything else, because a `quit` reaps it too
    (`server._teardown_runtime`: intent "quit" calls `reap_all_and_exit`). What
    this catches that it could not catch before: a `quit` that leaves the
    supervisor running, which would strand every session's process tree under a
    process nothing is left to address. What it still catches: any other child of
    `muxd` outliving `muxd`, exactly as before.

    `crash.log` is asserted *empty* rather than absent on purpose:
    `enable_crash_tracebacks` opens it for append during startup, so the file
    always exists and its emptiness is the signal. A faulthandler dump in it is
    a native crash that an exit code of zero would not have shown.
    """
    spec = daemon_spec(tmp_path)  # no `pty_supervisor_enabled` key: the shipped default
    async with supervisor_reaped(spec.data_dir), daemon_process(spec) as daemon:
        health = await daemon.wait_ready()
        assert_startup_is_complete(health)

        # The daemon reports through a socket it bound itself rather than through
        # a test server, so its own listener line is evidence the real path ran.
        listening = f"Running on http://127.0.0.1:{daemon.port}"
        assert listening in daemon.stdout.decode("utf-8", "replace"), daemon.diagnostics()

        # The default reached a supervisor, and the daemon says which process it
        # is. Asked of the daemon rather than of the discovery file because the
        # daemon is the one that has to be able to address it; the file is the
        # supervisor's claim about itself, and the two agreeing is a separate
        # fact worth one line.
        assert health["supervisor"] is True, (
            f"the shipped default did not reach a PTY supervisor: {health}\n"
            f"{daemon.diagnostics()}"
        )
        assert health["supervisor_state"] == "connected", health
        supervisor_pid = health["supervisor_pid"]
        assert isinstance(supervisor_pid, int) and supervisor_pid > 0, health
        assert alive(supervisor_pid), f"supervisor pid {supervisor_pid} was never alive"
        discovery = supervisor_discovery(daemon.data_dir)
        assert discovery is not None, "the supervisor wrote no discovery file"
        assert int(discovery["pid"]) == supervisor_pid, (health, discovery)

        descendants = [child.pid for child in daemon.descendants()]
        assert supervisor_pid in descendants, (
            f"the supervisor pid {supervisor_pid} is not among this daemon's children "
            f"{descendants}, so the survivor check below would never have seen it"
        )

        assert await daemon.request_shutdown("quit") == 202, daemon.diagnostics()
        returncode = await daemon.wait_for_exit()
        assert returncode == 0, f"muxd exited {returncode}\n{daemon.diagnostics()}"

        assert not alive(daemon.pid), "the daemon process outlived its own shutdown"
        await _assert_reaped(
            descendants,
            supervisor_pid=supervisor_pid,
            what="a `quit` shutdown must take every child of muxd with it, supervisor included",
        )

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


@pytest.mark.asyncio
async def test_a_supervised_shell_outlives_its_daemon_and_the_successor_adopts_it(
    tmp_path: Path,
) -> None:
    """The product's headline claim, end to end, on a machine that is not the operator's.

    "Live sessions survive a daemon restart" is what the supervisor exists for,
    what the tray's "Restart daemon (keep sessions)" promises, and what moving
    `pty_supervisor_enabled` to on by default was meant to make true for a fresh
    install. Until this test it was exercised only by hand, only on Windows, and
    only on the one machine where the flag had been turned on.

    The evidence is the marker, and it has to be. A successor that reported one
    live session and served a working terminal could be a daemon that respawned
    a *new* shell - which would be the exact failure the claim is about, and it
    renders identically. What no replacement process can produce is the first
    marker: those bytes exist only in the scrollback the original child wrote,
    which is held in the supervisor's memory and handed back on adoption. So the
    successor's attach must replay marker one, and the shell must then still
    answer marker two.

    Reaped through `supervisor_reaped`, not through either daemon's teardown: a
    supervisor is *designed* to outlive the daemon that spawned it, so the
    ordinary "reap what you started" rule has to be discharged one level up or a
    failure between the detach and the quit would leave a supervisor and a shell
    on the runner.
    """
    first = f"muxlive{uuid.uuid4().hex[:8]}"
    second = f"muxlive{uuid.uuid4().hex[:8]}"
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(subprocess.run, ["git", "init", "-q"], cwd=root, check=True)
    spec = daemon_spec(tmp_path)

    async with supervisor_reaped(spec.data_dir):
        async with daemon_process(spec) as daemon:
            health = await daemon.wait_ready()
            assert health["supervisor"] is True, daemon.diagnostics()
            supervisor_pid = int(health["supervisor_pid"])

            project_id = await daemon.register_project(root)
            sid = await daemon.spawn_shell(project_id, root, name="live-handoff")
            async with daemon.attach_pty(sid) as terminal:
                await until(
                    lambda: bool(terminal.output),
                    seconds=90.0,
                    what="the shell produced its first output",
                )
                # A fresh prompt, then quiet: input written while a shell is
                # still starting is dropped by the pseudoterminal and nothing
                # reports it.
                await terminal.settle()
                await terminal.send_input("\r")
                await terminal.settle()
                await terminal.send_input(f"echo {first}\r")
                await until(
                    lambda: terminal.text.count(first) >= 2,
                    seconds=90.0,
                    what=f"the shell echoed and ran `echo {first}`; saw {terminal.text[-400:]!r}",
                )

            # The session subtree, recorded while it is reachable. These are the
            # processes the final `quit` has to take, and they descend from the
            # supervisor rather than from either daemon.
            session_tree = [
                child.pid for child in psutil.Process(supervisor_pid).children(recursive=True)
            ]
            assert session_tree, "the supervisor owns no child processes for the live shell"

            daemon_descendants = [child.pid for child in daemon.descendants()]

            # "restart" is the detach intent: this daemon goes away and the
            # supervisor deliberately does not.
            assert await daemon.request_shutdown("restart") == 202, daemon.diagnostics()
            assert await daemon.wait_for_exit() == 0, daemon.diagnostics()
            assert not alive(daemon.pid), "the daemon outlived its own shutdown"

        assert alive(supervisor_pid), (
            "the supervisor did not survive a detaching shutdown, which is the "
            "one thing the whole feature rests on"
        )
        # Nothing is classified as a legitimate survivor *here*, on purpose. A
        # detach leaves a whole live subtree behind by design, and deciding
        # process by process which of them was allowed to stay is the inference
        # this file refuses to make - it would also have to know that a venv's
        # `python.exe` is a launcher whose own parent waits on it, which is true
        # on one platform and is not a fact about swe-mux. The leak question is
        # asked once, at the end, over the union of everything either daemon or
        # the supervisor ever owned: after the final `quit`, all of it is gone.

        async with daemon_process(spec) as successor:
            after = await successor.wait_ready()
            assert_startup_is_complete(after)
            assert after["supervisor_state"] == "connected", after
            assert int(after["supervisor_pid"]) == supervisor_pid, (
                "the successor started a *second* supervisor instead of adopting the "
                f"running one: {after['supervisor_pid']} != {supervisor_pid}"
            )
            assert after["live_sessions"] == 1, after
            assert after["supervisor_unadopted"] == 0, after
            assert sid in await successor.session_ids(), successor.diagnostics()

            async with successor.attach_pty(sid) as terminal:
                await until(
                    lambda: first in terminal.text,
                    seconds=90.0,
                    what=(
                        f"the successor replayed the original child's output ({first}); "
                        "without it, this session was respawned rather than adopted"
                    ),
                )
                await terminal.settle()
                await terminal.send_input(f"echo {second}\r")
                await until(
                    lambda: terminal.text.count(second) >= 2,
                    seconds=90.0,
                    what=(
                        f"the adopted shell echoed and ran `echo {second}`; "
                        f"saw {terminal.text[-400:]!r}"
                    ),
                )

            successor_descendants = [child.pid for child in successor.descendants()]
            assert await successor.request_shutdown("quit") == 202, successor.diagnostics()
            assert await successor.wait_for_exit() == 0, successor.diagnostics()

        await _assert_reaped(
            [*daemon_descendants, supervisor_pid, *session_tree, *successor_descendants],
            supervisor_pid=supervisor_pid,
            what=(
                "a `quit` must reap the supervisor it adopted and every session tree it held, "
                "even though this daemon did not start it, and must leave nothing behind from "
                "the daemon that detached either"
            ),
        )
