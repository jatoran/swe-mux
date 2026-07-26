from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import MethodType
from typing import Any

import pytest

from swe_mux.adapters.claude import ClaudeAdapter
from swe_mux.adapters.codex import CodexAdapter
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.launchers import resolve_command
from swe_mux.operational_telemetry import OperationalTelemetryStore, scan_native_telemetry
from swe_mux.provider_accounts import ProviderAccountError, ProviderAccountManager
from tests.support.detection_replay import DetectionReplay

RUN_LIVE = os.environ.get("SWEMUX_RUN_LIVE_AGENT_TESTS") == "1"
RUN_SUBAGENT = os.environ.get("SWEMUX_RUN_LIVE_SUBAGENT_TESTS") == "1"
RUN_PHASE2 = os.environ.get("SWEMUX_RUN_LIVE_PHASE2_TESTS") == "1"


def _executable(backend: str) -> str:
    if os.name == "nt" and backend == "codex":
        return shutil.which("codex.cmd") or resolve_command("codex.exe")
    return resolve_command(f"{backend}.exe")


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


@pytest.mark.live_agent
@pytest.mark.skipif(not RUN_LIVE, reason="set SWEMUX_RUN_LIVE_AGENT_TESTS=1")
@pytest.mark.parametrize("backend", ["claude", "codex"])
async def test_authenticated_provider_cli_completion_conforms_to_observer(
    backend: str, tmp_path: Path
) -> None:
    started = time.time()
    if backend == "claude":
        native_id = str(uuid.uuid4())
        adapter = ClaudeAdapter()
        command = [
            _executable("claude"),
            "--print",
            "--safe-mode",
            "--tools",
            "",
            "--session-id",
            native_id,
            "Reply with exactly: SWEMUX_CANARY_OK",
        ]
        _run(command, tmp_path)
        transcript = adapter.transcript_path(native_id, tmp_path)
    else:
        adapter = CodexAdapter()
        command = [
            _executable("codex"),
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--json",
            "Reply with exactly: SWEMUX_CANARY_OK",
        ]
        _run(command, tmp_path)
        candidates = adapter.recent_transcripts(tmp_path, started)
        assert candidates, "Codex completed without a discoverable root transcript"
        transcript = max(candidates)[1]
    assert transcript.exists()
    await _assert_transcript_conformance(backend, _records(transcript))


@pytest.mark.live_agent
@pytest.mark.live_subagent
@pytest.mark.skipif(not RUN_SUBAGENT, reason="set SWEMUX_RUN_LIVE_SUBAGENT_TESTS=1")
@pytest.mark.parametrize("backend", ["claude", "codex"])
async def test_authenticated_provider_subagent_signal_conforms_to_observer(
    backend: str, tmp_path: Path
) -> None:
    started = time.time()
    prompt = (
        "Spawn exactly one subagent. Ask it to return the word CHILD_OK, wait for it, "
        "then reply with exactly ROOT_OK. Do not edit files or run shell commands."
    )
    if backend == "claude":
        native_id = str(uuid.uuid4())
        adapter = ClaudeAdapter()
        command = [
            _executable("claude"),
            "--print",
            "--allowedTools",
            "Agent",
            "--permission-mode",
            "dontAsk",
            "--session-id",
            native_id,
            prompt,
        ]
        _run(command, tmp_path, timeout=180)
        transcript = adapter.transcript_path(native_id, tmp_path)
    else:
        adapter = CodexAdapter()
        command = [
            _executable("codex"),
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--json",
            prompt,
        ]
        _run(command, tmp_path, timeout=180)
        candidates = adapter.recent_transcripts(tmp_path, started)
        assert candidates, "Codex completed without a discoverable root transcript"
        transcript = max(candidates)[1]
    assert transcript.exists()
    await _assert_transcript_conformance(
        backend, _records(transcript), require_subagent=True
    )


@pytest.mark.live_agent
@pytest.mark.live_telemetry
@pytest.mark.skipif(not RUN_PHASE2, reason="set SWEMUX_RUN_LIVE_PHASE2_TESTS=1")
@pytest.mark.parametrize("backend", ["claude", "codex"])
async def test_authenticated_provider_phase2_telemetry_reaches_durable_store(
    backend: str, tmp_path: Path
) -> None:
    sentinel = tmp_path / "phase2-canary.txt"
    sentinel.write_text("SWEMUX_PHASE2_SENTINEL\n", encoding="utf-8")
    started = time.time()
    prompt = (
        "Use your read-only file or shell tool to read phase2-canary.txt in the current "
        "working directory. After the tool finishes, reply with exactly PHASE2_CANARY_OK."
    )
    if backend == "claude":
        native_id = str(uuid.uuid4())
        adapter = ClaudeAdapter()
        command = [
            _executable("claude"),
            "--print",
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            "Read",
            "--session-id",
            native_id,
            prompt,
        ]
        _run(command, tmp_path, timeout=180)
        transcript = adapter.transcript_path(native_id, tmp_path)
    else:
        adapter = CodexAdapter()
        command = [
            _executable("codex"),
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--json",
            prompt,
        ]
        _run(command, tmp_path, timeout=180)
        candidates = adapter.recent_transcripts(tmp_path, started)
        assert candidates, "Codex canary completed without a discoverable root transcript"
        transcript = max(candidates)[1]
    assert transcript.exists()
    await _assert_phase2_telemetry_conformance(backend, transcript, tmp_path)


@pytest.mark.live_quota
@pytest.mark.skipif(not RUN_PHASE2, reason="set SWEMUX_RUN_LIVE_PHASE2_TESTS=1")
@pytest.mark.parametrize("provider", ["claude", "codex"])
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
