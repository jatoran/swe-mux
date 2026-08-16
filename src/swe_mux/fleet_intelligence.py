from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .automation_store import AutomationStore
from .background_tasks import background
from .config import Config
from .delivery_readiness import DeliveryReadinessTracker
from .event_bus import EventBus
from .models import MuxEvent
from .processes import OwnedProcess, PreviewRegistry, ProcessInspector
from .session import Session, SessionManager
from .transcript_view import conversation_is_readable, parse_transcript_cached

# The claim check only reads the tail of a turn, so it never needs the whole
# conversation and must not pay for one on a long-running session.
_CLAIM_CHECK_MAX_BYTES = 256 * 1024


def _read_recent(
    path: Path | None, backend: str, native_id: str | None
) -> list[dict[str, Any]]:
    """Positional `parse_transcript_cached`, since `asyncio.to_thread` takes no keywords."""
    return parse_transcript_cached(
        path, backend, max_bytes=_CLAIM_CHECK_MAX_BYTES, native_id=native_id
    )

STALL_SECONDS = 300
UNATTENDED_SECONDS = 15
RUNAWAY_BYTES_PER_MINUTE = 4 * 1024 * 1024
# An interlock is a *condition*, not an event: it is announced once when it appears
# and re-armed only once it has been absent for this long. It used to re-announce on
# this same interval for as long as the condition held, which turned one true fact
# (two sessions sharing a dev server) into twelve identical records an hour for the
# life of both sessions. The window is a clear window, not a repeat window: it
# absorbs a sweep that misses a still-live connection instead of re-notifying.
INTERLOCK_CLEAR_SECONDS = 300
DIGEST_SECONDS = 30 * 60

FLEET_INSPECT_LOOP = "fleet-intelligence"
FLEET_EVENT_LOOP = "fleet-events"


class FleetIntelligence:
    """Deterministic cross-session evidence collector; it never actuates a session."""

    def __init__(
        self,
        sessions: SessionManager,
        events: EventBus,
        store: AutomationStore,
        processes: ProcessInspector,
        previews: PreviewRegistry,
        config: Config,
    ) -> None:
        self.sessions = sessions
        self.events = events
        self.store = store
        self.processes = processes
        self.previews = previews
        self.config = config
        self._task: asyncio.Task[None] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._claim_checks: set[asyncio.Task[None]] = set()
        self._queue: asyncio.Queue[MuxEvent] | None = None
        self._seen: dict[str, float] = {}
        # Interlock fingerprints whose condition is currently held, with the last
        # sweep that observed them. Presence here is what suppresses a re-announce.
        self._interlocks_active: dict[str, float] = {}
        self._failures: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._last_turn: dict[str, float] = {}
        self._turn_started: dict[str, float] = {}
        self._last_test: dict[str, float] = {}
        # Emit keys contributed per session, so a session's emit_once keys and held
        # interlock fingerprints can be dropped from both maps when it exits.
        self._emit_keys_by_session: dict[str, set[str]] = defaultdict(set)
        self._last_user_activity = time.time()
        self._last_digest = time.time()
        self.readiness = DeliveryReadinessTracker()
        # Optional back-reference: fleet already owns the one place that sees a
        # session end, so it is where per-session accumulators are dropped.
        self.automation: Any | None = None

    def start(self) -> None:
        if self._task:
            return
        self._queue = self.events.subscribe(name="fleet-intelligence")
        # Supervised: an unguarded fault here silently disables stall, unattended,
        # runaway, context-pressure, interlock and digest detection for the rest
        # of the daemon's (potentially weeks-long) lifetime.
        self._task = background.start(FLEET_INSPECT_LOOP, self._run)
        self._event_task = background.start(FLEET_EVENT_LOOP, self._consume)

    async def stop(self) -> None:
        if self._queue:
            self.events.unsubscribe(self._queue)
        await background.stop(FLEET_INSPECT_LOOP)
        await background.stop(FLEET_EVENT_LOOP)
        self._task = None
        self._event_task = None
        for task in tuple(self._claim_checks):
            task.cancel()
        await asyncio.gather(*self._claim_checks, return_exceptions=True)
        self._claim_checks.clear()

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            with background.iteration(FLEET_EVENT_LOOP):
                await self._consume_one(event)

    async def _consume_one(self, event: MuxEvent) -> None:
        session = (
            self.sessions.sessions.get(event.session_id)
            if event.session_id is not None
            else None
        )
        self.readiness.observe(event, session)
        if event.type in {"terminal_attached", "terminal_input"}:
            self._last_user_activity = event.ts
            await self.store.set_checkpoint("fleet:last_user_activity", {"ts": event.ts})
        if not event.session_id:
            return
        if event.type in {"session_exited", "session_crashed"}:
            sid = event.session_id
            self._failures.pop(sid, None)
            self._last_turn.pop(sid, None)
            self._turn_started.pop(sid, None)
            self._last_test.pop(sid, None)
            for key in self._emit_keys_by_session.pop(sid, ()):
                self._seen.pop(key, None)
                self._interlocks_active.pop(key, None)
            # The readiness tracker and the automation engine's source probes key
            # on the same sessions; without this their per-session memory grows
            # for the daemon's lifetime and skews the metrics read off them.
            self.readiness.forget(sid)
            if self.automation is not None:
                self.automation.forget_session(sid)
            return
        if event.type == "turn_ended":
            self._last_turn[event.session_id] = event.ts
            # asyncio keeps only a weak reference to a task; hold a strong one
            # so a claim check cannot be collected mid-flight, and so stop()
            # can cancel it.
            claim_task = asyncio.create_task(
                self._claim_check(event.session_id, event.ts),
                name=f"claim-check-{event.session_id}",
            )
            self._claim_checks.add(claim_task)
            claim_task.add_done_callback(self._claim_checks.discard)
        elif event.type == "turn_started":
            self._turn_started[event.session_id] = event.ts
        elif event.type == "tool_result" and event.payload.get("success", True):
            tool = str(event.payload.get("tool") or "").casefold()
            if any(token in tool for token in ("test", "pytest", "vitest", "check")):
                self._last_test[event.session_id] = event.ts
        elif event.type == "tool_result" and not event.payload.get("success", True):
            failures = self._failures[event.session_id]
            failures.append(
                {
                    "ts": event.ts,
                    "tool": str(event.payload.get("tool") or "unknown")[:120],
                    "exit_code": event.payload.get("exit_code"),
                }
            )
            while failures and event.ts - failures[0]["ts"] > 900:
                failures.popleft()
            same_tool = [item for item in failures if item["tool"] == failures[-1]["tool"]]
            session = self.sessions.sessions.get(event.session_id)
            if session and len(same_tool) >= 3:
                spiral_key = (
                    f"spiral:{session.record.agent_run_id}:"
                    f"{same_tool[-1]['tool']}:{int(event.ts // 900)}"
                )
                await self._emit_once(
                    spiral_key,
                    "stalled",
                    session,
                    [
                        {"signal": "repeated_tool_failure", "value": same_tool[-5:]},
                        {"signal": "failure_window_s", "value": 900},
                    ],
                    0.9,
                    subtype="spiral",
                )
            detail = str(event.payload.get("detail") or "")
            # Same project only, and only on an exact normalized error signature.
            # Widening to every project when the session has no trusted scope is
            # how a generic "command failed" ended up quoting an unrelated repo's
            # fix into this run's notes — the trust-poisoning failure the design
            # calls out by name.
            if detail and session and session.record.agent_run_id and (
                scope := session.record.trusted_scope_id
            ):
                matches = await self.store.experiences(
                    error=detail,
                    project_scope_id=scope,
                    limit=1,
                )
                if matches:
                    match = matches[0]
                    annotation = await self.store.create_annotation(
                        agent_run_id=session.record.agent_run_id,
                        session_id=event.session_id,
                        tag="prior-resolution",
                        content=(
                            "A prior run encountered a similar error. Suggested resolution: "
                            f"{match['resolution_summary']}"
                        ),
                        source_event_seq=event.seq,
                        rule_id=None,
                        rule_revision=None,
                        provenance="experience_index",
                        confidence=match.get("confidence"),
                    )
                    await self.events.emit(
                        "annotation_created",
                        session_id=event.session_id,
                        source="automation",
                        annotation_id=annotation["id"],
                        tag=annotation["tag"],
                        rule_id=None,
                    )

    async def _claim_check(self, session_id: str, ended_at: float) -> None:
        session = self.sessions.sessions.get(session_id)
        if not session:
            return
        native_id = session.record.native_session_id
        if not conversation_is_readable(
            session.transcript_path, session.record.backend, native_id
        ):
            return
        try:
            messages = await asyncio.wait_for(
                asyncio.to_thread(
                    _read_recent,
                    session.transcript_path,
                    session.record.backend,
                    native_id,
                ),
                timeout=2,
            )
        except (OSError, TimeoutError):
            return
        assistant = next(
            (item for item in reversed(messages) if item.get("role") == "assistant"), None
        )
        if not assistant:
            return
        text = " ".join(
            str(block.get("text") or "")
            for block in assistant.get("content") or []
            if block.get("type") == "text"
        )
        if not CLAIM_PATTERN.search(text):
            return
        turn_started = self._turn_started.get(session_id, ended_at - 3600)
        if self._last_test.get(session_id, 0) >= turn_started:
            return
        await self._emit_once(
            f"claim:{session.record.agent_run_id}:{int(ended_at)}",
            "claim_unverified",
            session,
            [
                {"signal": "completion_claim", "value": text[:240]},
                {"signal": "test_tool_seen_in_turn", "value": False},
            ],
            0.75,
        )

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(5)
            with background.iteration(FLEET_INSPECT_LOOP):
                await self.inspect()

    def _index_processes(self) -> dict[str, list[OwnedProcess]]:
        """Group live owned processes by session id once per tick.

        _attention and _interlocks would otherwise each re-scan the whole owned
        map (O(sessions x processes) per 5s tick). Only live rows (exited_at is
        None) are indexed, so the cpu sum and interlock filters keep excluding
        exited processes exactly as before.
        """
        by_session: dict[str, list[OwnedProcess]] = defaultdict(list)
        for item in self.processes.owned.values():
            if item.exited_at is None:
                by_session[item.session_id].append(item)
        return by_session

    async def inspect(self) -> None:
        now = time.time()
        index = self._index_processes()
        for session in list(self.sessions.sessions.values()):
            if not session.record.agent_run_id or session.record.state in {"exited", "crashed"}:
                continue
            await self._attention(session, now, index)
        await self._interlocks(now, index)
        if self.config.phase7_observers_enabled and now - self._last_digest >= DIGEST_SECONDS:
            self._last_digest = now
            items = await self.store.notifications(unread=True, limit=50)
            if items:
                evidence = [
                    {"signal": "unread_attention_records", "value": len(items)},
                    {"signal": "since_last_user_activity", "value": self._last_user_activity},
                ]
                notification = await self.store.notify(
                    agent_run_id=None,
                    session_id=None,
                    rule_id=None,
                    kind="attention_digest",
                    title="Attention digest",
                    message=f"{len(items)} unread attention records across the active fleet.",
                    severity="info",
                    evidence=evidence,
                )
                await self.events.emit(
                    "attention_digest_due",
                    source="automation",
                    since=self._last_user_activity,
                    items=len(items),
                )
                await self.events.emit(
                    "notification_created",
                    source="automation",
                    notification_id=notification["id"],
                    kind=notification["kind"],
                )

    async def _attention(
        self, session: Session, now: float, index: dict[str, list[OwnedProcess]] | None = None
    ) -> None:
        record = session.record
        run_id = record.agent_run_id or record.id
        if index is None:
            index = self._index_processes()
        process_rows = index.get(record.id, [])
        cpu = sum(item.cpu_pct for item in process_rows)
        if (
            record.state == "awaiting"
            and not session.subscribers
            and now - record.last_activity_ts >= UNATTENDED_SECONDS
        ):
            await self._emit_once(
                f"unattended:{run_id}",
                "unattended_attention",
                session,
                [
                    {"signal": "state", "value": "awaiting"},
                    {"signal": "browser_connections", "value": 0},
                ],
                1.0,
            )
        if record.state == "working" and now - record.last_activity_ts >= STALL_SECONDS and cpu < 5:
            failures = list(self._failures.get(record.id, ()))
            evidence: list[dict[str, Any]] = [
                {"signal": "pty_silence_s", "value": round(now - record.last_activity_ts)},
                {"signal": "process_cpu_pct", "value": round(cpu, 1)},
                {"signal": "state", "value": record.state},
            ]
            if failures:
                evidence.append({"signal": "recent_failures", "value": failures[-5:]})
            await self._emit_once(
                f"stalled:{run_id}:{int(now // STALL_SECONDS)}",
                "stalled",
                session,
                evidence,
                0.85 if failures else 0.7,
            )
        output_bytes = sum(size for _, size in session.output_window)
        if (
            output_bytes >= RUNAWAY_BYTES_PER_MINUTE
            and now - self._last_turn.get(record.id, 0) > 60
        ):
            await self._emit_once(
                f"runaway:{run_id}:{int(now // 60)}",
                "runaway",
                session,
                [
                    {"signal": "pty_bytes_60s", "value": output_bytes},
                    {
                        "signal": "seconds_since_turn",
                        "value": round(now - self._last_turn.get(record.id, 0)),
                    },
                ],
                0.8,
            )
        if record.context_pct >= 0.8:
            bucket = int(record.context_pct * 10)
            await self._emit_once(
                f"context:{run_id}:{bucket}",
                "context_pressure",
                session,
                [
                    {"signal": "context_pct", "value": record.context_pct},
                    {"signal": "measurement_source", "value": record.measurement_source},
                ],
                0.95 if record.measurement_source else 0.6,
                context_pct=record.context_pct,
            )

    async def _interlocks(
        self, now: float, index: dict[str, list[OwnedProcess]] | None = None
    ) -> None:
        if index is None:
            index = self._index_processes()
        # Re-arm before evaluating, never after: a condition that has been gone for
        # the clear window is forgotten here, so finding it again below is news.
        self._rearm_interlocks(now)
        live = [
            item
            for item in self.sessions.sessions.values()
            if item.record.agent_run_id and item.record.state not in {"exited", "crashed"}
        ]
        live_ids = {item.record.id for item in live}
        listeners: dict[tuple[str, int], set[str]] = defaultdict(set)
        registered_previews: dict[tuple[str, int], set[str]] = defaultdict(set)
        for preview in self.previews.items.values():
            if preview.session_id not in live_ids:
                continue
            key = (str(preview.host), int(preview.port))
            listeners[key].add(preview.session_id)
            registered_previews[key].add(preview.session_id)
        for sid, rows in index.items():
            if sid not in live_ids:
                continue
            for item in rows:
                for listener in item.listeners:
                    listeners[(str(listener.get("host")), int(listener.get("port") or 0))].add(sid)
        for (host, port), session_ids in listeners.items():
            if len(session_ids) < 2:
                continue
            await self._emit_interlock(
                "port_collision",
                sorted(session_ids),
                [
                    {"signal": "listener_host", "value": host},
                    {"signal": "listener_port", "value": port},
                    {
                        "signal": "registered_preview_sessions",
                        "value": sorted(registered_previews.get((host, port), set())),
                    },
                ],
                now,
                title="Port collision",
                message=(
                    f"{self._labels(sorted(session_ids))} are all listening on {host}:{port}."
                ),
            )
        providers_by_port: dict[int, set[str]] = defaultdict(set)
        preview_providers_by_port: dict[int, set[str]] = defaultdict(set)
        for preview in self.previews.items.values():
            if preview.session_id not in live_ids:
                continue
            providers_by_port[int(preview.port)].add(preview.session_id)
            preview_providers_by_port[int(preview.port)].add(preview.session_id)
        for sid, rows in index.items():
            if sid not in live_ids:
                continue
            for item in rows:
                for listener in item.listeners:
                    providers_by_port[int(listener.get("port") or 0)].add(item.session_id)
        for sid, rows in index.items():
            if sid not in live_ids:
                continue
            for item in rows:
                for connection in item.connections:
                    remote_host = str(connection.get("remote_host") or "")
                    remote_port = int(connection.get("remote_port") or 0)
                    if remote_host not in {"127.0.0.1", "::1"}:
                        continue
                    for provider_session in providers_by_port.get(remote_port, set()):
                        if provider_session == item.session_id:
                            continue
                        # Evidence only, deliberately. One session driving another's
                        # loopback server is how a second daemon, a preview, or a test
                        # harness is *supposed* to be exercised, so it is a fact worth
                        # recording on the bus and not a fault worth an attention record.
                        await self._emit_interlock(
                            "cross_session_dev_server",
                            sorted([provider_session, item.session_id]),
                            [
                                {"signal": "provider_session", "value": provider_session},
                                {"signal": "consumer_session", "value": item.session_id},
                                {"signal": "loopback_port", "value": remote_port},
                                {
                                    "signal": "provider_has_registered_preview",
                                    "value": provider_session
                                    in preview_providers_by_port.get(remote_port, set()),
                                },
                            ],
                            now,
                        )

    def _rearm_interlocks(self, now: float) -> None:
        """Forget held interlocks whose condition has been gone for the clear window.

        Only a fingerprint that leaves this map can be announced again, so the clear
        window is exactly how long a condition must stay resolved before its return
        counts as news. Every sweep that still sees the condition refreshes its entry.
        """
        for fingerprint, last_seen in list(self._interlocks_active.items()):
            if now - last_seen >= INTERLOCK_CLEAR_SECONDS:
                self._interlocks_active.pop(fingerprint, None)

    def _label(self, session_id: str) -> str:
        session = self.sessions.sessions.get(session_id)
        return (session.record.name if session else None) or session_id

    def _labels(self, session_ids: list[str]) -> str:
        return ", ".join(self._label(sid) for sid in session_ids)

    async def _emit_interlock(
        self,
        kind: str,
        session_ids: list[str],
        evidence: list[dict[str, Any]],
        now: float,
        *,
        title: str | None = None,
        message: str | None = None,
    ) -> None:
        """Announce an interlock once per appearance of its condition.

        A kind called without `title`/`message` is evidence only: it reaches the event
        bus — and so automation rules, the event stream, and the absence report — but
        never becomes an attention record. That is the difference between a fault and
        a fact, and only faults are worth interrupting a human for.
        """
        fingerprint = hashlib.sha256(
            json.dumps([kind, session_ids, evidence], sort_keys=True).encode()
        ).hexdigest()[:20]
        held = fingerprint in self._interlocks_active
        self._interlocks_active[fingerprint] = now
        if held:
            return
        for sid in session_ids:
            self._emit_keys_by_session[sid].add(fingerprint)
        await self.events.emit(
            "environment_interlock",
            session_id=session_ids[0],
            source="automation",
            kind=kind,
            sessions=session_ids,
            evidence=evidence,
            confidence=1.0,
        )
        if title is None or message is None:
            return
        # The emit above suspends, and the server pops sessions concurrently: a
        # bare index here is a live KeyError that used to kill the whole loop.
        owner = self.sessions.sessions.get(session_ids[0])
        notification = await self.store.notify(
            agent_run_id=owner.record.agent_run_id if owner else None,
            session_id=session_ids[0],
            rule_id=None,
            kind="environment_interlock",
            title=title,
            message=message,
            severity="warning",
            evidence=evidence,
        )
        await self.events.emit(
            "notification_created",
            session_id=session_ids[0],
            source="automation",
            notification_id=notification["id"],
            kind=notification["kind"],
        )

    async def _emit_once(
        self,
        key: str,
        event_type: str,
        session: Session,
        evidence: list[dict[str, Any]],
        confidence: float,
        **payload: Any,
    ) -> None:
        if key in self._seen:
            return
        self._seen[key] = time.time()
        self._emit_keys_by_session[session.record.id].add(key)
        await self.events.emit(
            event_type,
            session_id=session.record.id,
            source="automation",
            evidence=evidence,
            confidence=confidence,
            **payload,
        )

    async def absence_report(self, since: float | None = None) -> dict[str, Any]:
        checkpoint = await self.store.checkpoint("fleet:last_user_activity")
        start = since or float((checkpoint or {}).get("ts") or self._last_user_activity)
        annotations = [
            item for item in await self.store.annotations(limit=500) if item["created_at"] >= start
        ]
        notifications = [
            item
            for item in await self.store.notifications(limit=500)
            if item["created_at"] >= start
        ]
        # Phase 7.7: the scan timeline is the behavioral-summary substrate the
        # retired turn summarizer used to feed. Surface the spine written since
        # the absence began, attributed by run, so the away view is not left
        # blank where per-turn summaries used to be.
        scan_records = [
            {
                "id": item.get("id"),
                "agent_run_id": item.get("agent_run_id"),
                "session_id": item.get("session_id"),
                "project_id": item.get("project_id"),
                "t0": item.get("t0"),
                "t1": item.get("t1"),
                "work_phase": item.get("work_phase"),
                "summary": item.get("summary"),
                "intent": item.get("intent"),
                "blocked_on": item.get("blocked_on"),
            }
            for item in await self.store.scan_records(limit=500)
            if float(item.get("created_at") or 0.0) >= start
        ]
        sessions = []
        for session in self.sessions.sessions.values():
            if session.record.last_activity_ts >= start:
                sessions.append(
                    {
                        "session_id": session.record.id,
                        "agent_run_id": session.record.agent_run_id,
                        "name": session.record.name,
                        "backend": session.record.backend,
                        "state": session.record.state,
                        "last_activity": session.record.last_activity_ts,
                    }
                )
        return {
            "since": start,
            "generated_at": time.time(),
            "sessions": sessions,
            "annotations": annotations,
            "notifications": notifications,
            "scan_records": scan_records,
        }

    def injection_safety(self) -> dict[str, Any]:
        """Research-only evidence for a future actuation gate; never grants authority."""
        sessions = [self.readiness.evaluate(session) for session in self.sessions.sessions.values()]
        parsers = []
        for session in self.sessions.sessions.values():
            record = session.record
            total = record.parser_events_seen + record.parser_unknown_events
            parsers.append(
                {
                    "session_id": record.id,
                    "backend": record.backend,
                    "schema_version": record.parser_schema_version,
                    "status": record.parser_status,
                    "recognized": record.parser_events_seen,
                    "unknown": record.parser_unknown_events,
                    "unknown_rate": record.parser_unknown_events / total if total else None,
                    "unknown_signatures": dict(record.parser_unknown_signatures),
                    "diagnostic": record.parser_diagnostic,
                }
            )
        return {
            "version": 2,
            "research_only": True,
            "authorizes_actuation": False,
            "sessions": sessions,
            "shadow_metrics": self.readiness.metrics(),
            "parser_coverage": parsers,
        }


CLAIM_PATTERN = re.compile(r"\b(?:tests? (?:pass|passed)|implemented|fixed|complete[d]?)\b", re.I)
