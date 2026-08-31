"""swe-mux's own processes must never be owned - or signalled - by a session.

The bug these cover, observed 2026-08-31: a redeploy run from inside a codex session
launched the desktop shell, the parent walk correctly attributed it to that session,
and when the session was killed the shell escalated to `suspected_orphan` with
Terminate armed on it. The shell was the live UI window.

Reservation used to mean "a descendant of this daemon", which the shell is not: it is
the daemon's *parent*, and after one `reload-daemon` the successor daemon's parent pid
is the outgoing daemon's, dead within the second, so no live chain reaches the shell at
all. Identity - the very executable file this daemon is running - survives that.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.processes import (
    OwnedProcess,
    ProcessInspector,
    is_session_helper_command,
    own_executable,
)

APP_IMAGE = str(Path(r"D:\PROJECTS\swe-mux\dist\swe-mux\swe-mux.exe"))
OTHER_IMAGE = str(Path(r"C:\Program Files\nodejs\node.exe"))


class FakeProcess:
    """A process in a fake machine-wide table, with the identity reads the code makes."""

    def __init__(
        self,
        pid: int,
        parent_pid: int,
        created: float,
        *,
        image: str,
        argv: list[str] | None = None,
        rss: int = 1024,
    ) -> None:
        self.pid = pid
        self.parent_pid = parent_pid
        self.created = created
        self.image = image
        self.argv = argv if argv is not None else [image]
        self.rss = rss

    def oneshot(self) -> FakeProcess:
        return self

    def __enter__(self) -> FakeProcess:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def create_time(self) -> float:
        return self.created

    def ppid(self) -> int:
        return self.parent_pid

    def exe(self) -> str:
        return self.image

    def name(self) -> str:
        return Path(self.image).name

    def cmdline(self) -> list[str]:
        return self.argv

    def cpu_times(self) -> Any:
        return SimpleNamespace(user=0.0, system=0.0)

    def memory_info(self) -> Any:
        return SimpleNamespace(rss=self.rss)

    def children(self, recursive: bool = False) -> list[FakeProcess]:
        raise AssertionError("the parent map should be used, not children()")


def install_table(
    monkeypatch: pytest.MonkeyPatch,
    table: dict[int, FakeProcess],
    *,
    daemon_pid: int,
    ppid_map: bool = True,
) -> None:
    """Point `processes` at a fake machine whose parent table is exactly `table`."""
    from swe_mux import processes

    class NoSuchProcess(RuntimeError):
        pass

    class AccessDenied(RuntimeError):
        pass

    def build(pid: int) -> FakeProcess:
        found = table.get(int(pid))
        if found is None:
            raise NoSuchProcess(pid)
        return found

    def process_iter(attrs: list[str] | None = None) -> list[Any]:
        assert attrs == ["pid", "name"]
        return [
            SimpleNamespace(pid=pid, info={"pid": pid, "name": item.name()})
            for pid, item in sorted(table.items())
        ]

    namespace: dict[str, Any] = {
        "Process": build,
        "NoSuchProcess": NoSuchProcess,
        "AccessDenied": AccessDenied,
        "net_connections": lambda **_: [],
        "process_iter": process_iter,
    }
    if ppid_map:
        namespace["_ppid_map"] = lambda: {
            pid: item.parent_pid for pid, item in table.items()
        }
    monkeypatch.setattr(processes, "psutil", SimpleNamespace(**namespace))
    monkeypatch.setattr(processes.os, "getpid", lambda: daemon_pid)
    monkeypatch.setattr(processes, "own_executable", lambda: APP_IMAGE)


def inspector_for(
    sessions: Any = None, *, supervisor_pid: int | None = None
) -> ProcessInspector:
    manager = sessions if sessions is not None else SimpleNamespace(sessions={})
    if supervisor_pid is not None:
        manager.supervisor = SimpleNamespace(supervisor_pid=supervisor_pid)
    return ProcessInspector(cast(Any, manager), EventBus())


def claimed(pid: int, created: float, image: str) -> OwnedProcess:
    return OwnedProcess(
        pid,
        1,
        "session-a",
        Path(image).name,
        image,
        created,
        None,
        0,
        1024,
        [],
        [],
        attribution_source="parent_walk",
    )


def test_own_executable_is_only_a_thing_for_the_frozen_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dev daemon's `sys.executable` is a shared python.exe, not an identity."""
    from swe_mux import processes

    monkeypatch.delattr(processes.sys, "frozen", raising=False)
    assert own_executable() is None

    monkeypatch.setattr(processes.sys, "frozen", True, raising=False)
    monkeypatch.setattr(processes.sys, "executable", APP_IMAGE)
    assert own_executable() == str(Path(APP_IMAGE).resolve())


def test_a_session_spawned_app_helper_is_not_infrastructure() -> None:
    """`swe-mux.exe -m swe_mux.hook_client` runs on every tool call and is the session's."""
    assert is_session_helper_command(f"{APP_IMAGE} -m swe_mux.hook_client claude_stop")
    assert not is_session_helper_command(f"{APP_IMAGE} --daemon-child --config x.toml")
    assert not is_session_helper_command(APP_IMAGE)


def test_the_desktop_shell_is_reserved_even_with_no_live_link_to_the_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug: a reload-daemon leaves the shell reachable only by identity."""
    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE)
    # ppid 700 is the outgoing daemon a `reload-daemon` replaced; it is not in the
    # table because it exited, so an ancestor walk from 900 finds nothing.
    daemon = FakeProcess(900, 700, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    install_table(monkeypatch, {800: shell, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()
    row = claimed(800, 50.0, APP_IMAGE)
    inspector.owned[(800, 50.0)] = row

    inspector._collect_all()

    assert row.evidence_state == "stale"
    assert row.evidence_reason == "reserved_infrastructure_fingerprint"
    assert row.exit_evidence == "ownership_rejected"
    assert any(
        item["kind"] == "persisted_session_claimed_infrastructure"
        for item in inspector._ownership_diagnostics
    )


def test_a_session_helper_sharing_the_app_image_keeps_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reserving on the image alone would strip every hook client out of the fleet."""
    daemon = FakeProcess(900, 1, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    helper = FakeProcess(
        810,
        500,
        95.0,
        image=APP_IMAGE,
        argv=[APP_IMAGE, "-m", "swe_mux.hook_client", "claude_stop"],
    )
    install_table(monkeypatch, {810: helper, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()
    row = claimed(810, 95.0, APP_IMAGE)
    inspector.owned[(810, 95.0)] = row

    inspector._collect_all()

    assert row.exited_at is None
    assert row.evidence_reason != "reserved_infrastructure_fingerprint"


def test_a_foreign_process_is_never_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image test is an equality on one path, not a name or a directory match."""
    daemon = FakeProcess(900, 1, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    server = FakeProcess(400, 500, 95.0, image=OTHER_IMAGE)
    install_table(monkeypatch, {400: server, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()
    row = claimed(400, 95.0, OTHER_IMAGE)
    inspector.owned[(400, 95.0)] = row

    inspector._collect_all()

    assert row.exited_at is None


def test_the_supervisor_is_reserved_but_never_traversed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It parents every live session; descending through it would absorb the fleet."""
    daemon = FakeProcess(900, 1, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    supervisor = FakeProcess(950, 900, 91.0, image=str(Path(r"D:\s\swe-mux-supervisor.exe")))
    agent = FakeProcess(960, 950, 92.0, image=OTHER_IMAGE)
    grandchild = FakeProcess(961, 960, 93.0, image=OTHER_IMAGE)
    install_table(
        monkeypatch,
        {900: daemon, 950: supervisor, 960: agent, 961: grandchild},
        daemon_pid=900,
    )
    inspector = inspector_for(supervisor_pid=950)
    inspector._refresh_tree()

    reserved = {int(handle.pid) for handle in inspector._infrastructure_handles()}

    assert reserved == {900, 950}


def test_the_ancestor_walk_still_finds_the_shell_without_a_process_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`process_iter` is the same class of psutil private-ish dependency as `_ppid_map`."""
    from swe_mux import processes

    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE)
    daemon = FakeProcess(900, 800, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    install_table(monkeypatch, {800: shell, 900: daemon}, daemon_pid=900)
    monkeypatch.delattr(processes.psutil, "process_iter")
    inspector = inspector_for()
    inspector._refresh_tree()

    reserved = {int(handle.pid) for handle in inspector._infrastructure_handles()}

    assert reserved == {800, 900}


def test_the_shell_and_its_webview_land_in_the_daemon_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """swe-mux reported `processes: 1` for a frozen app whose shell alone held 69 MiB."""
    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE, rss=100)
    webview = FakeProcess(
        810, 800, 51.0, image=str(Path(r"C:\Edge\msedgewebview2.exe")), rss=200
    )
    daemon = FakeProcess(
        900, 800, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"], rss=400
    )
    install_table(monkeypatch, {800: shell, 810: webview, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()

    # startup=True: the first pass establishes infrastructure before any session exists.
    inspector._collect_all(startup=True)

    footer = inspector._daemon_resources
    assert footer["processes"] == 3
    assert footer["memory_bytes"] == 700
    assert {member["pid"] for member in footer["members"]} == {800, 810, 900}


def test_a_shell_nothing_points_at_is_still_enumerated_for_the_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live shape: reload-daemon left the shell with no link to the daemon at all."""
    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE, rss=100)
    webview = FakeProcess(
        810, 800, 51.0, image=str(Path(r"C:\Edge\msedgewebview2.exe")), rss=200
    )
    # ppid 700 exited with the outgoing daemon; nothing connects 900 to 800.
    daemon = FakeProcess(
        900, 700, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"], rss=400
    )
    install_table(monkeypatch, {800: shell, 810: webview, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()

    inspector._collect_all(startup=True)

    footer = inspector._daemon_resources
    assert {member["pid"] for member in footer["members"]} == {800, 810, 900}
    assert footer["memory_bytes"] == 700


def test_the_image_scan_is_not_repeated_on_every_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructing a psutil.Process is the most expensive thing in this file."""
    from swe_mux import processes

    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE)
    daemon = FakeProcess(900, 700, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    install_table(monkeypatch, {800: shell, 900: daemon}, daemon_pid=900)
    scans = 0
    real_iter = processes.psutil.process_iter

    def counting_iter(attrs: list[str] | None = None) -> list[Any]:
        nonlocal scans
        scans += 1
        return real_iter(attrs)

    monkeypatch.setattr(processes.psutil, "process_iter", counting_iter)
    inspector = inspector_for()

    inspector._collect_all(startup=True)
    inspector._collect_all(startup=True)
    inspector._collect_all(startup=True)

    assert scans == 1


def test_a_vanished_shell_is_rescanned_rather_than_enumerated_until_the_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recycled pid must not be enumerated as swe-mux for up to a minute."""
    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE)
    daemon = FakeProcess(900, 700, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    table = {800: shell, 900: daemon}
    install_table(monkeypatch, table, daemon_pid=900)
    inspector = inspector_for()
    inspector._collect_all(startup=True)
    assert inspector._own_image_pids == {800, 900}

    table.pop(800)
    inspector._collect_all(startup=True)

    assert inspector._own_image_pids == {900}
    assert {member["pid"] for member in inspector._daemon_resources["members"]} == {900}


def test_terminating_swe_mux_itself_is_refused_even_when_a_row_claims_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last gate before a signal, deliberately re-derived rather than trusted."""
    shell = FakeProcess(800, 1, 50.0, image=APP_IMAGE)
    daemon = FakeProcess(900, 700, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    install_table(monkeypatch, {800: shell, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()
    inspector.owned[(800, 50.0)] = claimed(800, 50.0, APP_IMAGE)

    with pytest.raises(ValueError, match="swe-mux itself"):
        inspector._owned_live("session-a", 800)


def test_a_real_session_process_stays_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate must refuse swe-mux and nothing else."""
    daemon = FakeProcess(900, 1, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    server = FakeProcess(400, 500, 95.0, image=OTHER_IMAGE)
    install_table(monkeypatch, {400: server, 900: daemon}, daemon_pid=900)
    inspector = inspector_for()
    inspector.owned[(400, 95.0)] = claimed(400, 95.0, OTHER_IMAGE)

    process, item = inspector._owned_live("session-a", 400)

    assert item.pid == 400
    assert process.pid == 400


def test_the_supervisor_boundary_survives_a_withdrawn_parent_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without `_ppid_map` the walk falls back to psutil, which has no boundary."""
    daemon = FakeProcess(900, 1, 90.0, image=APP_IMAGE, argv=[APP_IMAGE, "--daemon-child"])
    supervisor = FakeProcess(950, 900, 91.0, image=str(Path(r"D:\s\swe-mux-supervisor.exe")))
    agent = FakeProcess(960, 950, 92.0, image=OTHER_IMAGE)
    table = {900: daemon, 950: supervisor, 960: agent}
    install_table(monkeypatch, table, daemon_pid=900, ppid_map=False)
    daemon.children = lambda recursive=False: [supervisor, agent]  # type: ignore[method-assign]
    inspector = inspector_for(supervisor_pid=950)

    reserved = {int(handle.pid) for handle in inspector._infrastructure_handles()}

    assert reserved == {900, 950}
