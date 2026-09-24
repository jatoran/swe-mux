"""The process inspector writes only what changed, and never serializes on the loop.

Measured 2026-09-24: 7,484 retained processes, 7,377 of them exited, all rewritten to
`process_evidence` every ten seconds through `dataclasses.asdict` on the event loop.
That was about three quarters of the loop's busy time, in multi-second blocks - the
input lag and the daemon's brief unresponsive spells. These pin the replacement.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.processes import OwnedProcess, ProcessInspector, listener_record


def owned(pid: int, *, exited_at: float | None = None) -> OwnedProcess:
    return OwnedProcess(
        pid,
        1,
        "session-a",
        "worker.exe",
        "worker.exe --serve",
        10.0,
        exited_at,
        3.5,
        1024,
        [listener_record("0.0.0.0", 4000 + pid)],
        ["high_cpu"],
        connections=[
            {
                "local_host": "127.0.0.1",
                "local_port": 1,
                "remote_host": "127.0.0.1",
                "remote_port": 2,
            }
        ],
        parent_lineage=[{"pid": 1, "creation_time": 5.0}],
        identity_id=f"identity-{pid}",
        last_seen=100.0,
    )


class RecordingTelemetry:
    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.fail_next = False

    async def record_process_observations(self, observations: list[dict[str, Any]]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise OSError("database is locked")
        self.batches.append(observations)


def inspector_with(*processes: OwnedProcess) -> tuple[ProcessInspector, RecordingTelemetry]:
    telemetry = RecordingTelemetry()
    inspector = ProcessInspector(
        cast(Any, SimpleNamespace(sessions={})), EventBus(), telemetry=telemetry
    )
    for process in processes:
        inspector.owned[(process.pid, process.started_at or 0.0)] = process
    return inspector, telemetry


def test_snapshot_is_exactly_what_asdict_produced() -> None:
    process = owned(7)
    expected = dataclasses.asdict(process)
    expected["server_eligible"] = process.server_eligible()

    snapshot = process.snapshot()

    assert snapshot == expected
    # A copy, as asdict's was: mutating the snapshot must not reach the process.
    snapshot["listeners"][0]["port"] = 1
    snapshot["parent_lineage"].append({"pid": 2})
    assert process.listeners[0]["port"] == 4007
    assert process.parent_lineage == [{"pid": 1, "creation_time": 5.0}]


def test_the_observation_carries_every_field_the_store_reads() -> None:
    row = owned(7).observation()

    for name in (
        "pid", "started_at", "session_id", "identity_id", "agent_run_id", "project_id",
        "executable", "command_hash", "command", "parent_pid", "parent_lineage",
        "job_assignment", "evidence_state", "evidence_reason", "confidence", "first_seen",
        "last_seen", "last_verified_at", "exited_at", "exit_evidence", "inaccessible_count",
        "startup_revalidated", "attribution_version", "attribution_source",
        "last_attributed_at", "last_job_confirmed_at",
    ):
        assert name in row, name
    # Live readings are not evidence and are not written.
    assert "cpu_pct" not in row and "listeners" not in row


async def test_only_changed_processes_are_written_again() -> None:
    live, exited = owned(1), owned(2, exited_at=50.0)
    inspector, telemetry = inspector_with(live, exited)

    await inspector._persist_observations()
    await inspector._persist_observations()
    live.last_seen = 105.0
    await inspector._persist_observations()

    assert [sorted(row["pid"] for row in batch) for batch in telemetry.batches] == [[1, 2], [1]]


async def test_a_failed_write_is_retried_in_full() -> None:
    inspector, telemetry = inspector_with(owned(1), owned(2, exited_at=50.0))
    telemetry.fail_next = True

    with pytest.raises(OSError):
        await inspector._persist_observations()
    await inspector._persist_observations()

    assert [sorted(row["pid"] for row in batch) for batch in telemetry.batches] == [[1, 2]]


async def test_forgotten_processes_are_forgotten_by_the_write_tracker_too() -> None:
    process = owned(1)
    inspector, telemetry = inspector_with(process)
    await inspector._persist_observations()
    inspector.owned = {}
    await inspector._persist_observations()

    assert inspector._persisted == {}
    # Coming back (a restored fingerprint) is a change worth writing.
    inspector.owned[(1, 10.0)] = process
    await inspector._persist_observations()
    assert len(telemetry.batches) == 2


def test_infrastructure_loopback_ports_come_from_the_infrastructure_sample() -> None:
    inspector, _ = inspector_with()
    inspector._daemon_resources = {
        "members": [
            {
                "pid": 1,
                "listeners": [
                    listener_record("127.0.0.1", 8765),
                    listener_record("100.64.1.2", 8765),
                ],
            },
            {"pid": 2, "listeners": [listener_record("127.0.0.1", 49462)]},
        ]
    }

    assert inspector.infrastructure_loopback_ports() == frozenset({8765, 49462})
