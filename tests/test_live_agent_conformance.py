from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import MethodType
from typing import Any, Literal

import pytest

from swe_mux.adapters import build_agent_adapter
from swe_mux.event_bus import EventBus
from swe_mux.harness import (
    HARNESSES,
    descriptor,
    live_canary_harnesses,
    live_subagent_harnesses,
    live_telemetry_harnesses,
)
from swe_mux.history import HistoryIndex
from swe_mux.launchers import resolve_command
from swe_mux.operational_telemetry import OperationalTelemetryStore, scan_native_telemetry
from swe_mux.provider_accounts import PROVIDERS, ProviderAccountError, ProviderAccountManager
from tests.support.detection_replay import DetectionReplay

RUN_LIVE = os.environ.get("SWEMUX_RUN_LIVE_AGENT_TESTS") == "1"
RUN_SUBAGENT = os.environ.get("SWEMUX_RUN_LIVE_SUBAGENT_TESTS") == "1"
RUN_PHASE2 = os.environ.get("SWEMUX_RUN_LIVE_PHASE2_TESTS") == "1"


LIVE_HARNESSES = list(live_canary_harnesses())
LIVE_SUBAGENT_HARNESSES = list(live_subagent_harnesses())
LIVE_TELEMETRY_HARNESSES = list(live_telemetry_harnesses())
ProbeMode = Literal["read_only", "read_tool", "subagent"]


def _executable(backend: str) -> str:
    """The real CLI to invoke, bypassing mux's own generated shim.

    A harness that must be launched through its JS entrypoint cannot be reached by
    name on Windows, because the name resolves to a batch shim that mangles the argv
    it needs; its `.cmd` is asked for explicitly so the shell runs the shim rather
    than the daemon's resolver.
    """
    harness = descriptor(backend)
    if os.name == "nt" and harness.requires_direct_entrypoint:
        shim = shutil.which(f"{harness.script_base_name}.cmd")
        if shim:
            return shim
    return resolve_command(harness.executable)


def _probe_command(
    backend: str, prompt: str, native_id: str | None, mode: ProbeMode
) -> list[str]:
    """The full one-shot invocation for this harness in the requested mode.

    Composed from the descriptor rather than written per vendor: the headless argv
    and the spawn-id argv are both declared, so a harness added to the registry joins
    this tier without touching the canary.
    """
    harness = descriptor(backend)
    argv = getattr(harness.headless_probes, mode)
    assert argv is not None, f"{backend} declares no {mode} probe"
    command = [_executable(backend), *argv]
    if native_id and harness.spawn_id_argv:
        command.extend([*harness.spawn_id_argv, native_id])
    command.append(prompt)
    return command


async def _probe_transcript(
    backend: str,
    cwd: Path,
    started: float,
    prompt: str,
    mode: ProbeMode = "read_only",
) -> Path:
    """Run one real prompt and return the transcript the CLI wrote for it.

    Discovery follows the same declared split the daemon uses: where mux dictates the
    conversation id, the transcript is computed from it; where the CLI mints its own,
    the run's transcript is the newest one that appeared after it started.
    """
    harness = descriptor(backend)
    adapter = build_agent_adapter(
        harness,
        executable=_executable(backend),
        args=[],
        data_dir=cwd / "data",
        mcp_url="",
    )
    native_id = str(uuid.uuid4()) if harness.assigns_conversation_id else None
    _run(_probe_command(backend, prompt, native_id, mode), cwd, timeout=180)
    if native_id:
        path = adapter.transcript_path(native_id, cwd)
        assert path is not None, f"{backend} computed no transcript path"
        return path
    candidates = adapter.recent_transcripts(cwd, started)
    assert candidates, f"{backend} completed without a discoverable root transcript"
    return max(candidates)[1]


def _run(command: list[str], cwd: Path, timeout: int = 120) -> None:
    executable = Path(command[0])
    if os.name == "nt" and executable.suffix.casefold() in {".cmd", ".bat"}:
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *command]
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, f"provider CLI exited with {completed.returncode}"


def _records(path: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


async def _assert_transcript_conformance(
    backend: str, records: list[dict[str, object]], *, require_subagent: bool = False
) -> None:
    replay = DetectionReplay(backend)
    for record in records:
        await replay.transcript_record(record)
        await replay.drain()
    semantic = [item for item in replay.normalized if item["type"] != "state_changed"]
    event_types = {item["type"] for item in semantic}
    total = replay.session.record.parser_events_seen + replay.session.record.parser_unknown_events
    assert total > 0
    assert replay.session.record.parser_unknown_events / total < 0.25
    assert "turn_started" in event_types
    assert "turn_ended" in event_types
    if require_subagent:
        assert "subagent_activity" in event_types
    _assert_proven_status_shape(replay)


def _assert_proven_status_shape(replay: DetectionReplay) -> None:
    """Phase 3.5: a scripted real-CLI run must reach terminal status by proven
    evidence. Any inferred (watchdog/backstop) transition in the captured
    state-log means the current CLI no longer emits the terminal signals the
    observer relies on — exactly the drift this canary exists to catch."""
    transitions = [
        entry
        for entry in replay.session.state_transitions
        if entry.get("kind") == "transition" and entry["previous"] != entry["state"]
    ]
    assert transitions, "run produced no status transitions"
    inferred = [entry for entry in transitions if entry["proof"] != "proven"]
    assert not inferred, f"run needed inferred recoveries: {inferred}"
    assert replay.session.record.state == "idle", (
        f"run ended in {replay.session.record.state}, not proven idle"
    )
    terminal = transitions[-1]
    assert terminal["state"] == "idle"
    assert terminal["source"] in {"transcript", "hook"}
    assert terminal["evidence"], "terminal transition carried no evidence"
    health = replay.session.status_health()
    assert health["watchdog_recoveries"] == 0
    assert health["terminals"]["inferred"] == 0
    assert health["contract_violations"] == 0


async def _assert_phase2_telemetry_conformance(
    backend: str, transcript: Path, workspace: Path
) -> None:
    records = _records(transcript)
    replay = DetectionReplay(backend)
    for record in records:
        await replay.transcript_record(record)
        await replay.drain()

    event_types = {item["type"] for item in replay.normalized}
    assert "tool_use" in event_types
    assert "tool_result" in event_types
    assert replay.session.record.context_window > 0
    assert replay.session.record.context_pct > 0

    run_id = f"live-phase2-{backend}-{uuid.uuid4()}"
    scan = scan_native_telemetry(transcript, backend, run_id, "live-canary", None)
    assert any(item["kind"] == "tool_use" for item in scan["tools"])
    assert any(item["kind"] == "tool_result" for item in scan["tools"])
    total = scan["recognized"] + scan["unknown"]
    assert total > 0
    assert scan["unknown"] / total < 0.25

    database = workspace / f"phase2-{backend}.db"
    history = HistoryIndex(database)
    session = replay.session.record
    session.id = run_id
    session.agent_run_id = run_id
    session.project_id = "live-canary"
    session.cwd = str(workspace)
    session.created_at = transcript.stat().st_mtime
    session.last_activity_ts = time.time()
    await history.session_started(session, str(transcript))
    session.state = "exited"
    await history.session_ended(session, "live_canary_complete")

    store = OperationalTelemetryStore(database)
    store.history = history
    try:
        result = await store.reconcile_transcripts(limit=10)
        assert result == {"scanned": 1, "skipped": 0, "errors": 0}
        snapshot = await store.snapshot(limit=50)
        coverage = next(
            item
            for item in snapshot["tools"]["coverage"]
            if item["session_id"] == run_id
        )
        assert coverage["status"] == "ready"
        assert coverage["parser_version"] == snapshot["tools"]["parser_versions"][backend]
        assert coverage["tool_events"] >= 2
        assert any(item["session_id"] == run_id for item in snapshot["tools"]["metrics"])
        persisted = await history.history_entry(run_id)
        assert persisted is not None
        assert persisted["context_window"] == session.context_window
        assert persisted["final_context_pct"] == pytest.approx(session.context_pct)
    finally:
        store.close()
        history.close()


def test_every_transcript_harness_is_covered_by_the_live_canary() -> None:
    """A harness must not reach the live tier by having no live test.

    Runs without an authenticated CLI, because it asserts about the *parametrization*
    rather than about a provider. The canary used to name two vendors, so three later
    harnesses had no live coverage and nothing reported the hole; deriving the set
    means a new harness either joins it or states on its descriptor why it cannot.
    """
    for name, harness in HARNESSES.items():
        if harness.transcript_dialect is None:
            # No transcript to replay: this canary's assertion does not exist for it.
            # Its measurement is a database read, covered by its own store tests.
            assert name not in LIVE_HARNESSES, name
            continue
        assert harness.headless_probes.read_only is not None, (
            f"{name} writes a transcript but declares no headless probe, so the live "
            f"canary cannot run against its real CLI"
        )
        assert name in LIVE_HARNESSES, name
    assert LIVE_HARNESSES, "the live canary covers no harness at all"
    # The richer tiers are subsets, and each exclusion is a declared capability gap
    # rather than a skip: pi ships no subagent tool, so it runs the base canary and
    # the telemetry canary but is never asked to spawn a child.
    assert set(LIVE_SUBAGENT_HARNESSES) <= set(LIVE_HARNESSES)
    assert set(LIVE_TELEMETRY_HARNESSES) <= set(LIVE_HARNESSES)
    for name in LIVE_HARNESSES:
        probes = HARNESSES[name].headless_probes
        assert (probes.subagent is not None) == (name in LIVE_SUBAGENT_HARNESSES), name
        assert (probes.read_tool is not None) == (name in LIVE_TELEMETRY_HARNESSES), name


@pytest.mark.live_agent
@pytest.mark.skipif(not RUN_LIVE, reason="set SWEMUX_RUN_LIVE_AGENT_TESTS=1")
@pytest.mark.parametrize("backend", LIVE_HARNESSES)
async def test_authenticated_provider_cli_completion_conforms_to_observer(
    backend: str, tmp_path: Path
) -> None:
    transcript = await _probe_transcript(
        backend, tmp_path, time.time(), "Reply with exactly: SWEMUX_CANARY_OK"
    )
    assert transcript.exists()
    await _assert_transcript_conformance(backend, _records(transcript))


@pytest.mark.live_agent
@pytest.mark.live_subagent
@pytest.mark.skipif(not RUN_SUBAGENT, reason="set SWEMUX_RUN_LIVE_SUBAGENT_TESTS=1")
@pytest.mark.parametrize("backend", LIVE_SUBAGENT_HARNESSES)
async def test_authenticated_provider_subagent_signal_conforms_to_observer(
    backend: str, tmp_path: Path
) -> None:
    transcript = await _probe_transcript(
        backend,
        tmp_path,
        time.time(),
        "Spawn exactly one subagent. Ask it to return the word CHILD_OK, wait for it, "
        "then reply with exactly ROOT_OK. Do not edit files or run shell commands.",
        mode="subagent",
    )
    assert transcript.exists()
    await _assert_transcript_conformance(
        backend, _records(transcript), require_subagent=True
    )


@pytest.mark.live_agent
@pytest.mark.live_telemetry
@pytest.mark.skipif(not RUN_PHASE2, reason="set SWEMUX_RUN_LIVE_PHASE2_TESTS=1")
@pytest.mark.parametrize("backend", LIVE_TELEMETRY_HARNESSES)
async def test_authenticated_provider_phase2_telemetry_reaches_durable_store(
    backend: str, tmp_path: Path
) -> None:
    sentinel = tmp_path / "phase2-canary.txt"
    sentinel.write_text("SWEMUX_PHASE2_SENTINEL\n", encoding="utf-8")
    transcript = await _probe_transcript(
        backend,
        tmp_path,
        time.time(),
        "Use your read-only file or shell tool to read phase2-canary.txt in the current "
        "working directory. After the tool finishes, reply with exactly PHASE2_CANARY_OK.",
        mode="read_tool",
    )
    assert transcript.exists()
    await _assert_phase2_telemetry_conformance(backend, transcript, tmp_path)


@pytest.mark.live_quota
@pytest.mark.skipif(not RUN_PHASE2, reason="set SWEMUX_RUN_LIVE_PHASE2_TESTS=1")
@pytest.mark.parametrize("provider", list(PROVIDERS))
async def test_authenticated_provider_quota_schema_without_credential_mutation(
    provider: str, tmp_path: Path
) -> None:
    manager = ProviderAccountManager(tmp_path / "accounts", EventBus(), home=Path.home())
    auth_path = manager._system_auth_path(provider)  # type: ignore[arg-type]
    if not auth_path.is_file():
        pytest.skip(f"{provider} system credentials are unavailable")
    original_auth_bytes = auth_path.read_bytes()
    _, auth = manager._read_json_auth(auth_path)

    async def refuse_claude_refresh(
        self: ProviderAccountManager, current: dict[str, Any]
    ) -> dict[str, Any] | None:
        del self, current
        raise ProviderAccountError("live quota canary refuses credential refresh")

    async def refuse_codex_fallback(
        self: ProviderAccountManager, current: dict[str, Any], account_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        del self, current, account_id
        return None, None

    try:
        if provider == "claude":
            manager._refresh_claude_auth = MethodType(  # type: ignore[method-assign]
                refuse_claude_refresh, manager
            )
            quota, updated = await manager._fetch_claude(auth)
        else:
            manager._fetch_codex_rpc = MethodType(  # type: ignore[method-assign]
                refuse_codex_fallback, manager
            )
            account_id = str(manager._identity("codex", auth).get("provider_account_id") or "live")
            quota, updated = await manager._fetch_codex(auth, account_id)
        assert updated is None
        assert quota["source"] in {"oauth", "backend"}
        assert quota["session"] is not None or quota["weekly"] is not None
        for window in (quota["session"], quota["weekly"]):
            if window is not None:
                assert 0 <= window["used_percent"] <= 100

        database = tmp_path / f"quota-{provider}.db"
        store = OperationalTelemetryStore(database)
        try:
            await store.record_quota_sample(
                provider=provider,
                account_id="live-canary",
                quota={**quota, "status": "ready"},
                sampled_at=time.time(),
                account_active=True,
                auth_state="saved",
            )
        finally:
            store.close()
        reopened = OperationalTelemetryStore(database)
        try:
            latest = await reopened.latest_quota_by_account()
            assert latest["live-canary"]["status"] == "ready"
        finally:
            reopened.close()
    finally:
        await manager.stop()
        assert auth_path.read_bytes() == original_auth_bytes
