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
from swe_mux.adapters.opencode import OpenCodeAdapter
from swe_mux.event_bus import EventBus
from swe_mux.harness import (
    HARNESSES,
    descriptor,
    live_canary_harnesses,
    live_store_harnesses,
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
LIVE_STORE_HARNESSES = list(live_store_harnesses())
LIVE_SUBAGENT_HARNESSES = list(live_subagent_harnesses())
LIVE_TELEMETRY_HARNESSES = list(live_telemetry_harnesses())
ProbeMode = Literal["read_only", "read_tool", "subagent", "automations"]


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


_OUTPUT_HEAD = 1500
_OUTPUT_TAIL = 1500


def _bounded(stream: bytes | None, label: str) -> str:
    """One captured CLI stream, bounded to a readable prefix and tail.

    A provider CLI can print megabytes (or a single enormous JSON line), so the
    whole stream cannot go into a failure message. Both ends are kept because the
    two carry different evidence: the prefix names the model and the flags the run
    actually resolved, and the tail carries the error that ended it.
    """
    if not stream:
        return f"{label}: <empty>"
    text = stream.decode("utf-8", errors="replace").strip()
    if len(text) <= _OUTPUT_HEAD + _OUTPUT_TAIL:
        return f"{label}:\n{text}"
    elided = len(text) - _OUTPUT_HEAD - _OUTPUT_TAIL
    return (
        f"{label} (bounded, {elided} chars elided):\n"
        f"{text[:_OUTPUT_HEAD]}\n...[{elided} chars elided]...\n{text[-_OUTPUT_TAIL:]}"
    )


def _run(
    command: list[str],
    cwd: Path,
    timeout: int = 120,
    *,
    env: dict[str, str] | None = None,
) -> str:
    executable = Path(command[0])
    if os.name == "nt" and executable.suffix.casefold() in {".cmd", ".bat"}:
        # cmd.exe /c splits an argument at its first newline, so a multi-line prompt
        # reaches a .cmd-launched harness (codex, pi) truncated at the first break --
        # and the run then behaves oddly on a prompt it never fully received. Fail
        # loudly here rather than let that truncation pass silently.
        multiline = [arg for arg in command if "\n" in arg or "\r" in arg]
        assert not multiline, (
            "a multi-line argument is truncated by cmd.exe for a .cmd-launched "
            f"harness; keep the invocation (prompt included) on one line: {multiline!r}"
        )
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *command]
    # Captured, not discarded: a red run in this tier used to say only "exited with
    # 1", which is unfalsifiable evidence — it cannot distinguish an auth expiry from
    # a rate limit from a flag the CLI stopped accepting. The capture is bounded on
    # both ends so an enormous stream still fits in a failure message.
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as expired:
        raise AssertionError(
            f"provider CLI did not finish within {timeout}s\n"
            f"{_bounded(expired.stdout, 'stdout')}\n{_bounded(expired.stderr, 'stderr')}"
        ) from expired
    output = f"{_bounded(completed.stdout, 'stdout')}\n{_bounded(completed.stderr, 'stderr')}"
    assert completed.returncode == 0, (
        f"provider CLI exited with {completed.returncode}\n{output}"
    )
    return output


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
        has_probe = harness.headless_probes.read_only is not None
        writes_transcript_file = (
            harness.transcript_dialect is not None and harness.reports_transcript_path
        )
        if writes_transcript_file:
            # A file to replay: the transcript canary drives it.
            assert has_probe, (
                f"{name} writes a transcript but declares no headless probe, so the "
                f"live canary cannot run against its real CLI"
            )
            assert name in LIVE_HARNESSES, name
            assert name not in LIVE_STORE_HARNESSES, name
        elif harness.measurement_source == "database":
            # A store row instead of a file: the store canary drives it.
            assert has_probe, (
                f"{name} measures from a store but declares no headless probe, so the "
                f"store canary cannot run against its real CLI"
            )
            assert name in LIVE_STORE_HARNESSES, name
            assert name not in LIVE_HARNESSES, name
        else:
            # Neither a transcript file nor a store measurement: nothing to drive.
            assert name not in LIVE_HARNESSES, name
            assert name not in LIVE_STORE_HARNESSES, name
    assert LIVE_HARNESSES, "the live canary covers no harness at all"
    assert LIVE_STORE_HARNESSES, "the store canary covers no harness at all"
    # Every harness with a headless probe is covered by exactly one live tier, so a
    # new harness cannot land with a probe and no live coverage of either shape.
    for name, harness in HARNESSES.items():
        if harness.headless_probes.read_only is not None:
            assert (name in LIVE_HARNESSES) ^ (name in LIVE_STORE_HARNESSES), name
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


#: How many times the store canary will drive the CLI before giving up on a
#: provider that never answered. Two, not more: a retry buys past one rotation of
#: an unhealthy endpoint, and a longer loop only spends the operator's quota
#: waiting out an outage the tier cannot fix.
_STORE_ATTEMPTS = 2

#: The provider's relay saying it could not reach a model — opencode's own wording
#: for it, captured live 2026-08-24 ("Error from provider (Console): Upstream
#: request failed: Endpoint is unavailable."). Deliberately narrow: these phrases
#: name a third party's availability, and nothing here matches the failures the
#: canary exists to catch (a flag the CLI stopped accepting, an expired
#: credential, a store that was never written), which must still be red.
_PROVIDER_OUTAGE_MARKERS = (
    "error from provider",
    "upstream request failed",
    "endpoint is unavailable",
)


def _is_provider_outage(output: str) -> bool:
    """Whether a CLI failure was the provider being unreachable, not a mux fact."""
    lowered = output.casefold()
    return any(marker in lowered for marker in _PROVIDER_OUTAGE_MARKERS)


def test_provider_outage_is_distinguished_from_a_real_cli_failure() -> None:
    """The retry/skip path must not swallow the failures this tier is for.

    Runs in the default tier, because the classifier deciding when a live run is
    *not* evidence is exactly the thing that must not drift unwatched: widen it and
    the store canary silently stops guarding the measurement path.
    """
    assert _is_provider_outage(
        "provider CLI exited with 1\nstderr:\n> build - big-pickle\n"
        "Error: Error from provider (Console): Upstream request failed: "
        "Endpoint is unavailable."
    )
    for real_failure in (
        "provider CLI exited with 1\nstderr: error: unknown option '--json'",
        "provider CLI exited with 1\nstderr: Authentication failed: run `opencode auth login`",
        "provider CLI exited with 2\nstdout: <empty>\nstderr: <empty>",
        "provider CLI did not finish within 180s\nstdout: <empty>\nstderr: <empty>",
    ):
        assert not _is_provider_outage(real_failure), real_failure


def _newest_store_session_id(database: Path) -> str | None:
    """The newest root conversation id opencode wrote to its store.

    A store-backed harness mints its own id, so a live test cannot pre-seed one:
    the run is discovered after the fact. Subagents are child rows (`parent_id`
    set), excluded here so the assertion targets the run the canary drove.
    """
    import sqlite3

    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id FROM session WHERE parent_id IS NULL "
            "ORDER BY time_updated DESC, time_created DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    return str(row["id"]) if row else None


@pytest.mark.live_agent
@pytest.mark.skipif(not RUN_LIVE, reason="set SWEMUX_RUN_LIVE_AGENT_TESTS=1")
@pytest.mark.parametrize("backend", LIVE_STORE_HARNESSES)
async def test_authenticated_store_backed_cli_measurement_reaches_the_adapter(
    backend: str, tmp_path: Path
) -> None:
    """A store-backed harness proves its live path through the store, not a file.

    The transcript canary cannot see opencode: it writes rows, not a file. This is
    the derived-store sibling — run the real CLI headless into an isolated store,
    then read the `session` row through the adapter, so a break in the exact-figure
    measurement path is caught against the real binary. `OPENCODE_DATA_DIR` isolates
    the store so the run never lands in the operator's real `~/.local/share/opencode`.
    """
    harness = descriptor(backend)
    assert harness.measurement_source == "database", backend
    command = _probe_command(
        backend, "Reply with exactly: SWEMUX_STORE_OK", None, "read_only"
    )
    # opencode's CLI writes its store under XDG_DATA_HOME/opencode, honouring the
    # XDG base-directory spec even on Windows. OPENCODE_DATA_DIR (what mux's own
    # resolver reads) does not steer the CLI's write, so isolating the run means
    # setting XDG_DATA_HOME and pointing the adapter at the `opencode` subdirectory.
    #
    # One store per attempt: a run that died upstream can still have written a
    # partial `session` row, and the read below takes the newest root row — sharing
    # one directory across attempts is how a retry comes to measure the corpse of
    # the attempt before it.
    refusals: list[str] = []
    for attempt in range(_STORE_ATTEMPTS):
        store_dir = tmp_path / f"store-{attempt}"
        store_dir.mkdir()
        data_home = store_dir / "opencode"
        env = {**os.environ, "XDG_DATA_HOME": str(store_dir)}
        try:
            output = _run(command, tmp_path, timeout=180, env=env)
            break
        except AssertionError as failure:
            if not _is_provider_outage(str(failure)):
                raise
            refusals.append(str(failure))
    else:
        # Every attempt died in the provider's own relay, before the CLI had a turn
        # to measure. There is no mux fact in that, and asserting one would make a
        # third party's outage read as a break in the measurement path — so the tier
        # says what happened and stops, rather than failing or passing on nothing.
        pytest.skip(
            f"{backend}'s provider was unreachable on {_STORE_ATTEMPTS} attempts; "
            f"last: {refusals[-1][-1500:]}"
        )

    database = data_home / (harness.conversation_store_file or "")
    assert database.exists(), f"{backend} wrote no store at {database}\n{output}"
    native_id = _newest_store_session_id(database)
    assert native_id is not None, (
        f"{backend} recorded no conversation in its store\n{output}"
    )

    adapter = OpenCodeAdapter(data_home=data_home)
    measurements = adapter.session_measurements(native_id)
    assert measurements is not None, f"{backend} adapter read no session row\n{output}"
    # The measurement path, not the provider's pricing policy. A real turn always
    # produces output tokens and names the model that produced them, whichever model
    # opencode rotated to, so those two stay strict. Cost is asserted as present and
    # numeric rather than nonzero: a zero-priced model (a free tier, a
    # subscription-covered one) writes 0.0 into the same column a priced one writes
    # 0.0031 into, so a nonzero demand would fail on a provider decision.
    assert int(measurements["tokens_out"] or 0) > 0, f"{measurements}\n{output}"
    assert measurements["model"], f"{measurements}\n{output}"
    assert isinstance(measurements["cost_usd"], float), f"{measurements}\n{output}"


def _codex_subagent_envelopes(records: list[dict[str, object]]) -> set[str]:
    """Which record envelope carried codex's subagent signal in this run.

    ``sub_agent_activity`` is the payload codex wrote through 2026-08-06;
    ``item_completed`` is where 0.149 nests the identical fields, under an ``item``
    typed ``SubAgentActivity``. Naming the envelope is what turns the next move
    into "the shape changed to X" instead of "there is no subagent signal", which
    is the report this canary owed and did not give when the move happened.
    """
    seen: set[str] = set()
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "sub_agent_activity":
            seen.add("sub_agent_activity")
        item = payload.get("item")
        if (
            payload.get("type") == "item_completed"
            and isinstance(item, dict)
            and item.get("type") == "SubAgentActivity"
        ):
            seen.add("item_completed")
    return seen


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
    records = _records(transcript)
    if backend == "codex":
        # The current CLI's envelope, asserted as itself. The derived assertion
        # below is satisfied by either shape, so without this a third move would
        # again be reported only as an absence.
        envelopes = _codex_subagent_envelopes(records)
        assert "item_completed" in envelopes, (
            "codex wrote no `item_completed`/`SubAgentActivity` record; envelopes "
            f"seen: {sorted(envelopes) or 'none'}"
        )
    await _assert_transcript_conformance(backend, records, require_subagent=True)


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
