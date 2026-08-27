"""What the daemon can say about its own health, and the doctor report."""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..background_tasks import background
from ..config import Config
from ..console_contention import ConsoleCensus, probe_console_participants
from ..deterministic_consumers import DeterministicConsumerService
from ..errors import NotFound
from ..event_bus import EventBus
from ..harness import (
    detect_installations_with_versions,
    public_harness_registry,
)
from ..http_support import json_response
from ..loop_lag import LoopLagMonitor
from ..network_usage import (
    NetworkUsage,
    request_peer,
)
from ..operational_telemetry import OperationalTelemetryStore
from ..posix_firewall import inspect_posix_firewall, posix_firewall_supported
from ..prerequisites import detect_prerequisites
from ..scrollback import SCREEN_TAIL_BYTES
from ..session import (
    SessionManager,
    pty_tail_explain,
    session_cli_state_status,
    session_is_unwitnessed,
)
from ..status_timeline import StatusTimelineStore
from ..storage_usage import StorageUsage
from ..tailscale import (
    tailscale_ipv4,
    tailscale_status,
)
from ..tier0_store import Tier0Store
from ..transcript_view import (
    conversation_is_readable,
)
from ..ui_build import read_ui_build_id
from ..windows_firewall import (
    firewall_supported,
    inspect_firewall,
)
from . import history
from .support import _query_epoch

log = logging.getLogger(__name__)


def _live_state_log_payload(
    app: Any, session: Any, now: float, console_census: ConsoleCensus | None = None
) -> dict[str, Any]:
    """The live half of the state-log: current fields plus the in-memory rings.

    ``console_census`` is passed in rather than measured here because it is the
    one field that walks a process tree, and this function is synchronous and
    runs on the event loop. Callers await it on a thread; ``None`` renders as an
    unmeasured census rather than as an absent key, so the shape is stable.
    """
    transcript = session.transcript_path
    transcript_mtime: float | None = None
    if transcript is not None:
        try:
            transcript_mtime = transcript.stat().st_mtime
        except OSError:
            transcript_mtime = None
    try:
        pty_tail = session.scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace")
        pty_explanation = pty_tail_explain(
            pty_tail,
            backend=session.record.backend,
            cli_state_status=session_cli_state_status(session),
            osc_title=session.osc_signals.title,
            osc_progress=session.osc_signals.progress,
        )
    except (AttributeError, OSError, ValueError):
        pty_explanation = {
            "outcome": "unknown",
            "screen_outcome": "unknown",
            "outcome_source": "screen",
            "cli_state_status": session_cli_state_status(session),
            "rules": [],
        }
    raw_hook_sequences = session.observation_state.get("hook_sequences", {})
    hook_sequences = dict(raw_hook_sequences) if isinstance(raw_hook_sequences, dict) else {}
    return {
        "id": session.record.id,
        "live": True,
        "backend": session.record.backend,
        "state": session.record.state,
        "state_detail": session.record.state_detail,
        "state_source_priority": session.state_source_priority,
        "now": now,
        "last_state_change_ts": session.last_state_change_ts,
        "seconds_in_state": round(now - session.last_state_change_ts, 3),
        "last_hook_ts": session.last_hook_ts or None,
        "transcript_path": str(transcript) if transcript else None,
        "transcript_mtime": transcript_mtime,
        # Whether that path was proven or guessed. A provisional binding drives
        # state only, so a session reporting live turns with no tokens and a
        # placeholder conversation id is explained by this field and not a bug.
        "transcript_provisional": session.transcript_provisional,
        # No transcript and no hook ever: the PTY screen is the only source
        # that can move this session, which is what licenses the
        # begin/end_pty_turn watchdog pair.
        "unwitnessed": session_is_unwitnessed(session),
        "observer_restart_count": session.observer_restart_count,
        "observer_last_fault": session.observer_last_fault,
        "watchdog_recoveries": session.watchdog_recoveries,
        "hook_sequences": hook_sequences,
        "hook_sequence_duplicates": session.observation_state.get("hook_sequence_duplicates", 0),
        "observation_replay": session.observation_replay,
        # The one fault class that presents as a perfectly healthy session:
        # the transcript parses fine, it is just not this PTY's conversation
        # any more. Paired with the run counter, because "which conversation
        # am I looking at" is the question this endpoint exists to answer.
        "observation_stale_since": session.record.observation_stale_since,
        "observation_stale_reason": getattr(session, "observation_stale_reason", None),
        "observation_diagnostic": session.record.observation_diagnostic,
        # When the tailer last saw that file grow, which is what staleness is
        # actually decided on. Reported beside `transcript_mtime` because the pair
        # is the diagnosis: a frozen `transcript_mtime` next to a recent
        # `transcript_growth_ts` is a filesystem that stopped dating a live file
        # (routine for Codex rollouts on Windows), not a replaced conversation.
        "transcript_growth_ts": session.transcript_growth_ts or None,
        # Newest trustworthy timestamp carried by a record in the followed file.
        # This stays old across replay, unlike an observer-attach timestamp.
        "transcript_record_ts": getattr(session, "transcript_record_ts", 0.0) or None,
        "agent_run_id": session.record.agent_run_id,
        "agent_run_seq": session.record.agent_run_seq,
        "native_session_id": session.record.native_session_id,
        "agent_lifecycle_id": session.agent_lifecycle_id,
        "awaiting_reason": session.record.awaiting_reason,
        "standing_activity": [activity.snapshot() for activity in session.record.standing_activity],
        # Start of the current stretch of running work, and the turn fields it is
        # read against. Reported together because the diagnosis is the trio: an
        # anchor far older than `last_turn_ms` on a session with no open turn is a
        # harness that handed off to background agents, which is exactly when the
        # turn alone stops answering "how long has this been going".
        "running_work_since": session.record.running_work_since,
        "turn_started_at": session.record.turn_started_at,
        "last_turn_ms": session.record.last_turn_ms,
        # The CLI's own published state for this conversation
        # (~/.claude/sessions/<pid>.json) — corroboration only, never a
        # transition source. None until the poller matches a file.
        "cli_state": session.cli_state,
        # Last observed reading per detection-ladder layer; the flips behind
        # these values are ledgered as `layer_reading` timeline entries.
        "layer_readings": dict(session.layer_readings),
        "pty_explain": pty_explanation,
        # Who is reading this pane's pseudoconsole. A session spawned as a shell
        # and promoted around an agent typed into it has two processes that could
        # be, and exactly one that may (`console_contention.py`). Walked here
        # rather than sampled continuously: it answers a yes/no question about two
        # pids and only matters when someone is asking. `contention` is the
        # standing verdict, set by the daemon when a shell prompt arrives under a
        # live agent; a census with `agent_in_pty_tree: false` beside a live
        # `agent_pid` is the orphaned-wrapper shape.
        "console": {
            "contention": session.record.console_contention,
            "agent_launch_pending": list(session.record.agent_launch_pending),
            "census": (
                console_census
                or ConsoleCensus(None, None, None, None, error="not_measured")
            ).snapshot(),
        },
        "status_health": session.status_health(now),
        # Multi-device terminal arbitration. Non-zero rejections mean keystrokes
        # arrived from a client that had lost input ownership; non-zero denials
        # mean a background pane tried to take it back.
        "input_arbitration": {
            # The device classes the daemon believes are in use: a passive claim
            # from any other one is refused, so this is the first thing to check
            # when input ownership is not where it is expected to be.
            "active_devices": sorted(app[keys.DEVICE_PRESENCE].active_profiles()),
            "leading_device": app[keys.DEVICE_PRESENCE].leading_profile(),
            "owner_device": session.input_owner_device,
            "owner_epoch": session.input_owner_epoch,
            "attached_viewports": len(session.viewports),
            "geometry": list(session.geometry) if session.geometry else None,
            "input_rejections": session.input_rejections,
            "claim_denials": session.input_claim_denials,
            "claims": list(session.claim_log),
        },
        # Real state changes are kept separately: a busy turn emits dozens
        # of same-state tool detail updates that would otherwise evict the
        # history explaining how the session reached its current state.
        "state_changes": list(session.state_changes),
        "transitions": list(session.state_transitions),
    }


async def _post_mortem_state_log(
    app: Any, sid: str, from_ts: float | None, to_ts: float | None
) -> web.Response:
    """State-log for a session that no longer exists, from the durable store.

    The timeline table records (session_id, agent_run_id) pairs, so the mux id
    or any of its run ids (a history row's key) reaches the same rows.
    """
    store: StatusTimelineStore = app[keys.STATUS_TIMELINE]
    timeline, truncated = await store.timeline(sid, from_ts=from_ts, to_ts=to_ts)
    history_row = await app[keys.HISTORY].history_entry(sid)
    if not timeline and not history_row:
        raise NotFound(sid, kind="session")
    runs = await store.runs_for_session(sid)
    if not runs and history_row:
        runs = await store.runs_for_session(str(history_row["id"]))
    return json_response(
        {
            "id": sid,
            "live": False,
            "history": history_row,
            "runs": runs,
            "from": from_ts,
            "to": to_ts,
            "timeline": timeline,
            "timeline_truncated": truncated,
            "timeline_sink": store.stats(),
        }
    )


async def _session_console_census(session: Any) -> ConsoleCensus:
    """Walk this pane's process tree off the event loop."""
    return await asyncio.to_thread(
        probe_console_participants,
        session.record.pid if session.record.pid > 0 else None,
        session.record.agent_process_pid,
    )


async def get_session_state_log(request: web.Request) -> web.Response:
    """End-detection diagnostics: transitions, faults, watchdog and layer activity.

    With `from`/`to` (epoch seconds) the response adds a `timeline` slice from
    the durable store — the live ring is flushed first, so the slice is
    complete up to the moment of the request. For an ended session the durable
    timeline is the whole answer (post-mortem mode).
    """
    sid = request.match_info["sid"]
    from_ts = _query_epoch(request, "from")
    to_ts = _query_epoch(request, "to")
    try:
        session = request.app[keys.SESSIONS].resolve(sid)
    except KeyError:
        return await _post_mortem_state_log(request.app, sid, from_ts, to_ts)
    now = time.time()
    payload = _live_state_log_payload(
        request.app, session, now, await _session_console_census(session)
    )
    store: StatusTimelineStore = request.app[keys.STATUS_TIMELINE]
    if from_ts is not None or to_ts is not None:
        await store.flush_session(session)
        timeline, truncated = await store.timeline(session.record.id, from_ts=from_ts, to_ts=to_ts)
        payload["from"] = from_ts
        payload["to"] = to_ts
        payload["timeline"] = timeline
        payload["timeline_truncated"] = truncated
    payload["timeline_sink"] = store.stats()
    return json_response(payload)


# Bounded transcript slice per agent run in a diagnostic bundle; the timeline
# names the moments, the transcript shows what the agent was actually doing.
DIAGNOSTIC_BUNDLE_MAX_MESSAGES_PER_RUN = 200


DIAGNOSTIC_BUNDLE_DEFAULT_WINDOW_SECONDS = 3600.0


async def _bundle_transcript_slices(
    app: Any,
    run_ids: list[str],
    from_ts: float,
    to_ts: float,
) -> list[dict[str, Any]]:
    """Native-timestamped transcript records inside the window, per agent run.

    Reuses history indexing: each run id is a history row whose transcript
    path is parsed through the shared cache; messages are filtered by their
    native timestamps. Runs whose transcript is gone report that instead of
    silently vanishing from the bundle.
    """
    from ..history import _message_timestamp

    slices: list[dict[str, Any]] = []
    for run_id in run_ids:
        row = await app[keys.HISTORY].history_entry(run_id)
        if not row:
            slices.append({"agent_run_id": run_id, "error": "no history row"})
            continue
        transcript = row.get("transcript_path")
        path = Path(str(transcript)) if transcript else None
        native_id = str(row.get("native_id") or "") or None
        if not conversation_is_readable(path, str(row["backend"]), native_id):
            slices.append({"agent_run_id": run_id, "error": "native transcript is unavailable"})
            continue
        try:
            parsed = await asyncio.to_thread(
                history._parse_conversation, path, str(row["backend"]), native_id
            )
        except (OSError, ValueError):
            slices.append({"agent_run_id": run_id, "error": "native transcript is unreadable"})
            continue
        in_window: list[dict[str, Any]] = []
        for message in parsed.messages:
            ts = _message_timestamp(message.get("ts"))
            if ts is None or ts < from_ts or ts > to_ts:
                continue
            in_window.append({**message, "ts_epoch": ts})
        truncated = len(in_window) > DIAGNOSTIC_BUNDLE_MAX_MESSAGES_PER_RUN
        slices.append(
            {
                "agent_run_id": run_id,
                "transcript_path": str(transcript),
                "messages": in_window[:DIAGNOSTIC_BUNDLE_MAX_MESSAGES_PER_RUN],
                "abandoned_messages": parsed.abandoned,
                "truncated": truncated,
            }
        )
    return slices


async def get_session_diagnostic_bundle(request: web.Request) -> web.Response:
    """One-fetch investigation artifact for a status incident.

    Packages, for the requested window (default: the last hour): the durable
    detection timeline, the current state-log fields (live sessions), the
    fleet status-health aggregate, and the transcript records whose native
    timestamps fall inside the window. STATUS_INCIDENT_RUNBOOK.md documents
    how to read it.
    """
    from ..session import fleet_status_health

    sid = request.match_info["sid"]
    now = time.time()
    to_ts = _query_epoch(request, "to")
    from_ts = _query_epoch(request, "from")
    if to_ts is None:
        to_ts = now
    if from_ts is None:
        from_ts = to_ts - DIAGNOSTIC_BUNDLE_DEFAULT_WINDOW_SECONDS
    store: StatusTimelineStore = request.app[keys.STATUS_TIMELINE]
    session = None
    try:
        session = request.app[keys.SESSIONS].resolve(sid)
    except KeyError:
        pass
    state_log: dict[str, Any] | None = None
    identity = sid
    if session is not None:
        await store.flush_session(session)
        state_log = _live_state_log_payload(
            request.app, session, now, await _session_console_census(session)
        )
        identity = session.record.id
    timeline, truncated = await store.timeline(identity, from_ts=from_ts, to_ts=to_ts)
    history_row = await request.app[keys.HISTORY].history_entry(identity)
    if session is None and not timeline and not history_row:
        raise NotFound(sid, kind="session")
    # Every run the window touches: the timeline's own run keys, plus the live
    # run and the history row for sessions the durable rows do not (yet) cover.
    run_ids = [str(entry["agent_run_id"]) for entry in timeline]
    if session is not None:
        run_ids.append(str(session.record.agent_run_id or session.record.id))
    if history_row:
        run_ids.append(str(history_row["id"]))
    ordered_runs = list(dict.fromkeys(run_ids))
    transcripts = await _bundle_transcript_slices(request.app, ordered_runs, from_ts, to_ts)
    return json_response(
        {
            "id": identity,
            "live": session is not None,
            "from": from_ts,
            "to": to_ts,
            "now": now,
            "state_log": state_log,
            "history": history_row,
            "runs": ordered_runs,
            "timeline": timeline,
            "timeline_truncated": truncated,
            "timeline_sink": store.stats(),
            "fleet_status_health": fleet_status_health(
                request.app[keys.SESSIONS].sessions.values()
            ),
            "transcripts": transcripts,
        }
    )


async def get_status_health(request: web.Request) -> web.Response:
    """Fleet status-health diagnostic: inferred-recovery counts, bounds, alarm.

    A healthy fleet reaches terminal status by proven evidence; a rise in
    inferred recoveries, a contract violation, or a session stuck active past
    the bound raises the alarm the soak matrix asserts on.
    """
    from ..session import fleet_status_health

    return json_response(fleet_status_health(request.app[keys.SESSIONS].sessions.values()))


async def get_background_health(request: web.Request) -> web.Response:
    """Background-task diagnostic: which long-lived loops are alive and faulting.

    Every loop is supervised (restart with capped backoff) and every event-bus
    drop is attributed, so a dead poller or a starved consumer is visible here
    rather than presenting as a feature that quietly stopped working.
    """
    tier0: Tier0Store = request.app[keys.TIER0]
    events: EventBus = request.app[keys.EVENTS]
    consumers: DeterministicConsumerService = request.app[keys.DETERMINISTIC_CONSUMERS]
    loop_lag: LoopLagMonitor = request.app[keys.LOOP_LAG]
    return json_response(
        {
            **background.health(),
            # Read this before the per-loop numbers when the question is "why does the
            # UI feel slow". Loop cost tells you which subsystem is expensive; this
            # tells you whether anything is blocking the thread every request, frame
            # and keystroke shares.
            "loop_lag": loop_lag.snapshot(),
            "event_bus": events.drop_stats(),
            "tier0_capture": tier0.capture_stats(),
            "git_provenance": request.app[keys.GIT_PROVENANCE].status(),
            # A detector that stopped producing findings is indistinguishable from
            # a quiet fleet unless the loop's own liveness is reported.
            "deterministic_consumers": consumers.status(),
            # Ranking that stopped routing looks exactly like a quiet fleet from
            # the inbox, so its counters and its loop liveness are reported here.
            "attention_ranking": request.app[keys.ATTENTION_RANKING].status(),
            "attention_narration": request.app[keys.ATTENTION_NARRATOR].status(),
            "project_contexts": request.app[keys.PROJECT_CONTEXTS].status(),
            "scan_timeline": request.app[keys.SCAN_TIMELINE].status(),
            "mcp": request.app[keys.MCP].status(),
            # A watch service that stopped resolving is indistinguishable from a
            # fleet nobody is watching, so its counters and open count are here.
            "session_watch": request.app[keys.SESSION_WATCH].status(),
        }
    )


async def get_notification_diagnostics(request: web.Request) -> web.Response:
    """Content-free notification planner and delivery outcomes for a recent window."""

    try:
        days = float(request.query.get("days", "7"))
    except ValueError as exc:
        raise ValueError("days must be a number") from exc
    telemetry: OperationalTelemetryStore = request.app[keys.TELEMETRY]
    if not math.isfinite(days) or days <= 0 or days > telemetry.retention_days:
        raise ValueError(
            f"days must be greater than zero and no more than {telemetry.retention_days}"
        )
    until = time.time()
    return json_response(
        await telemetry.notification_decision_summary(
            since=until - days * 86400,
            until=until,
        )
    )


async def get_network_usage(request: web.Request) -> web.Response:
    """Application-payload counters for one daemon boot or explicit measurement window."""

    meter: NetworkUsage = request.app[keys.NETWORK_USAGE]
    return json_response(meter.snapshot())


async def reset_network_usage(request: web.Request) -> web.Response:
    """Start a fresh measurement window without restarting the daemon or any session."""

    meter: NetworkUsage = request.app[keys.NETWORK_USAGE]
    previous = meter.snapshot()
    totals = previous["totals"]
    log.info(
        "network usage counters reset by peer %s after %.1fs: "
        "http_rx=%d http_tx=%d ws_rx=%d ws_tx=%d",
        request_peer(request),
        previous["uptime_seconds"],
        totals["http"]["request_bytes"],
        totals["http"]["response_bytes"],
        totals["websocket"]["received_bytes"],
        totals["websocket"]["sent_bytes"],
    )
    meter.reset()
    return json_response({"reset": True, "previous": previous})


async def get_storage_usage(request: web.Request) -> web.Response:
    """swe-mux's on-disk footprint: data-dir buckets plus per-Project `.swe-mux`.

    Read-only measurement of the bytes swe-mux stores, never the host drive's
    capacity. The walk is I/O-heavy (the WebView2 cache dominates), so it runs
    off the event loop and behind a TTL cache; `?refresh=1` forces a re-measure.
    """
    storage: StorageUsage = request.app[keys.STORAGE_USAGE]
    force = request.query.get("refresh", "").lower() in {"1", "true", "yes"}
    report = await asyncio.to_thread(storage.snapshot, force=force)
    response = json_response(report)
    response.headers["Cache-Control"] = "no-store"
    return response


# Log tails are bounded so the copyable blob stays pasteable and the read is
# cheap. daemon.log and redeploy.log are the two records a first-connect or
# redeploy failure leaves behind.
_DIAGNOSTICS_LOG_TAIL_BYTES = 64 * 1024


def _log_tail(path: Path, max_bytes: int = _DIAGNOSTICS_LOG_TAIL_BYTES) -> dict[str, object]:
    """Read the last ``max_bytes`` of a log file as decoded lines, best-effort."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read()
    except OSError as exc:
        return {"path": str(path), "present": False, "error": str(exc), "lines": []}
    text = data.decode("utf-8", "replace")
    return {
        "path": str(path),
        "present": True,
        "bytes": size,
        "truncated": size > max_bytes,
        "lines": text.splitlines(),
    }


async def diagnostics_export(request: web.Request) -> web.Response:
    """One copyable blob aggregating what a first-connect report always needs.

    Bundles the sanitized config, remote-connection state, firewall status,
    network counters, the fleet status-health aggregate, and the tails of
    daemon.log and redeploy.log. Everything here is already sanitized: the config
    goes through ``public_dict`` (no secrets), and the two logs are command-free
    by design, so no terminal bytes or message content are ever included. Mirrors
    Orca's ``buildConnectionDiagnosticsReport`` intent, adapted to swe-mux's
    daemon-side pieces.
    """
    from ..session import fleet_status_health

    config: Config = request.app[keys.CONFIG]
    sessions: SessionManager = request.app[keys.SESSIONS]
    meter: NetworkUsage = request.app[keys.NETWORK_USAGE]
    store: StatusTimelineStore = request.app[keys.STATUS_TIMELINE]
    supervisor = request.app.get(keys.SUPERVISOR)

    remote = await tailscale_status(config.port, tailnet_enabled=config.tailnet_enabled)
    firewall: dict[str, object] = {"supported": False}
    if firewall_supported():
        serve_active = bool(
            remote.get("serve_configured") or remote.get("mobile_voice_configured")
        )
        firewall = await inspect_firewall(
            config.port, await tailscale_ipv4(), serve_active=serve_active
        )
    elif posix_firewall_supported():
        firewall = await inspect_posix_firewall(config.port, await tailscale_ipv4())

    live = sum(session.pty.isalive() for session in sessions.sessions.values())
    export = {
        "generated_at": time.time(),
        "swe_mux_version": "0.1.0",
        "platform": {
            "system": sys.platform,
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "daemon": {
            "port": config.port,
            "host": config.host,
            "data_dir": str(config.data_dir),
            "live_sessions": live,
            "supervisor_state": (
                "connected"
                if supervisor is not None and supervisor.connected
                else "lost"
                if supervisor is not None and getattr(supervisor, "lost", False)
                else "absent"
            ),
        },
        "config": config.public_dict(),
        "remote_status": remote,
        "firewall": firewall,
        "network_usage": meter.snapshot(),
        "status_health": fleet_status_health(sessions.sessions.values()),
        "status_timeline_sink": store.stats(),
        # Counters only. The recovery store also holds terminal bytes, which are
        # whatever the child printed and therefore never leave the machine in an
        # export - the same reason scrollback itself is not in this bundle.
        "session_recovery_sink": (
            recovery_store.stats()
            if (recovery_store := request.app.get(keys.SESSION_RECOVERY)) is not None
            else None
        ),
        "cold_sessions": [
            {
                "id": session.record.id,
                "name": session.record.name,
                "backend": session.record.backend,
                "project_id": session.record.project_id,
                "cold_since": session.record.cold_since,
                "cold_reason": session.record.cold_reason,
                "terminal_captured_at": session.record.cold_terminal_at,
                "terminal_skipped": session.record.cold_terminal_skipped,
                "terminal_bytes": session.scrollback.size,
            }
            for session in sessions.sessions.values()
            if session.record.cold
        ],
        "logs": {
            "daemon": _log_tail(config.data_dir / "daemon.log"),
            "redeploy": _log_tail(config.data_dir / "redeploy.log"),
        },
    }
    response = json_response(export)
    response.headers["Cache-Control"] = "no-store"
    return response


async def get_doctor_report(request: web.Request) -> web.Response:
    """Consolidated read-only diagnostics: `mux doctor` without --export.

    One structured report over the diagnostics the daemon already serves plus the
    observation-freshness check that nothing else exposes. Assembly is pure and
    lives in `doctor.py`; the gathering is `_doctor_report`, shared with the
    configurator's own diagnostics tool so the two can never disagree about this
    install's health. Everything here is already sanitized (public config,
    connection state, firewall, status health) and the freshness rows are
    content-free, so no secret, terminal byte, or message body is ever included.
    """
    response = json_response(await _doctor_report(request.app))
    response.headers["Cache-Control"] = "no-store"
    return response


async def _doctor_report(app: web.Application) -> dict[str, Any]:
    """Gather every diagnostic payload and assemble the report."""
    from ..doctor import build_doctor_report, observation_freshness
    from ..session import fleet_status_health

    config: Config = app[keys.CONFIG]
    sessions: SessionManager = app[keys.SESSIONS]
    supervisor = app.get(keys.SUPERVISOR)

    live = sum(session.pty.isalive() for session in sessions.sessions.values())
    supervisor_state = (
        "connected"
        if supervisor is not None and supervisor.connected
        else "lost"
        if supervisor is not None and getattr(supervisor, "lost", False)
        else "absent"
    )
    health = {
        "ok": True,
        "version": "0.1.0",
        "live_sessions": live,
        "ui_build_id": read_ui_build_id(app[keys.FRONTEND_DIR]),
        "supervisor_state": supervisor_state,
        "supervisor_unadopted": int(
            getattr(sessions, "unadopted_supervisor_sessions", 0) or 0
        ),
    }
    remote = await tailscale_status(config.port, tailnet_enabled=config.tailnet_enabled)
    firewall: dict[str, Any] = {"supported": False}
    if firewall_supported():
        serve_active = bool(
            remote.get("serve_configured") or remote.get("mobile_voice_configured")
        )
        firewall = await inspect_firewall(
            config.port, await tailscale_ipv4(), serve_active=serve_active
        )
    elif posix_firewall_supported():
        # The POSIX equivalent is a reachability probe plus the exact command this
        # host's firewall tool would need, never a rule edit: opening a port is a
        # security decision that requires root, and a daemon must not make it.
        firewall = await inspect_posix_firewall(config.port, await tailscale_ipv4())
    prerequisites = await asyncio.to_thread(detect_prerequisites)
    installations = await asyncio.to_thread(
        detect_installations_with_versions, dict(config.harness_exe)
    )
    now = time.time()
    report: dict[str, Any] = build_doctor_report(
        health=health,
        remote=remote,
        firewall=firewall,
        prerequisites=prerequisites,
        status_health=fleet_status_health(sessions.sessions.values()),
        background=background.health(),
        harnesses=public_harness_registry(installations),
        freshness=observation_freshness(sessions.sessions.values(), now=now),
        platform={
            "system": sys.platform,
            "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        daemon={"host": config.host, "port": config.port},
        now=now,
        wsl_bridges=await asyncio.to_thread(_wsl_bridge_report, config),
    )
    return report


def _wsl_bridge_report(config: Any) -> list[dict[str, Any]]:
    """Bridge status per WSL distribution, including when the bridge is switched off.

    The first version returned nothing unless `wsl_bridge_enabled` was already on,
    which made the diagnostic silent in exactly the situation it exists for: a host
    with WSL and a native agent in it, where the user has no idea the bridge is
    possible. A check that only speaks after you have already found the feature is
    not a check.

    What stays true off the opt-in is that it does not *probe*. Inspecting a
    distribution starts it, and a diagnostics read must not spend seconds booting
    something the user did not ask about. So an off host reports one row per
    distribution saying the bridge is available to enable, and a running
    distribution is still inspected cheaply because it costs nothing extra.
    """
    from ..wsl_bridge import cached_bridge_status, list_distros, running_distros, wsl_available

    if not wsl_available():
        return []
    enabled = bool(getattr(config, "wsl_bridge_enabled", False))
    running = running_distros()
    rows: list[dict[str, Any]] = []
    for distro in list_distros():
        if enabled or distro in running:
            # Reachability is only meaningful once the daemon is actually meant to
            # be listening, so the port is passed only when the feature is on.
            row = cached_bridge_status(
                distro, daemon_port=config.port if enabled else None
            ).as_dict()
            # Stamped on every row, not just the ones skipped below: a running
            # distribution on a host with the bridge switched off still has real
            # findings, and without this the reader cannot tell whether "not
            # available" means broken or simply not turned on.
            row["enabled"] = enabled
            if not enabled:
                existing = row.get("reasons")
                reasons = [str(item) for item in existing] if isinstance(existing, list) else []
                row["reasons"] = ["the WSL agent bridge is switched off", *reasons]
            rows.append(row)
            continue
        rows.append(
            {
                "distro": distro,
                "available": False,
                "enabled": False,
                "harnesses": [],
                "installed": False,
                "reachable": None,
                "reasons": [
                    "the WSL agent bridge is off; enable it in Settings to run an agent "
                    f"natively inside {distro} and have mux observe it"
                ],
            }
        )
    return rows


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/sessions/{sid}/state-log", get_session_state_log),
    web.get("/api/sessions/{sid}/diagnostic-bundle", get_session_diagnostic_bundle),
    web.get("/api/diagnostics/status-health", get_status_health),
    web.get("/api/diagnostics/background", get_background_health),
    web.get("/api/diagnostics/notifications", get_notification_diagnostics),
    web.get("/api/diagnostics/network", get_network_usage),
    web.delete("/api/diagnostics/network", reset_network_usage),
    web.get("/api/diagnostics/storage", get_storage_usage),
    web.get("/api/diagnostics/export", diagnostics_export),
    web.get("/api/diagnostics/doctor", get_doctor_report),
)
