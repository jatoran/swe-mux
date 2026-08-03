from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent, SessionRecord, SessionState
from swe_mux.observation import (
    IncrementalJsonlDecoder,
    _claude,
    _codex,
    _finish_transcript_catchup,
    _record_parser_observation,
    apply_hook_observation,
    classify_transcript_event,
    foreign_conversation_hook_id,
    tail_turn_state,
)
from swe_mux.screen_mode import BracketedPasteParser, ScreenModeParser
from swe_mux.session import (
    STATE_CHANGE_LOG_LIMIT,
    STATE_TRANSITION_LOG_LIMIT,
    apply_state_transition,
    apply_watchdog_recovery,
    expire_standing_activity,
    note_classifier_blindness,
    pty_tail_state,
    session_is_unwitnessed,
    session_status_health,
    startup_dialog_observation,
    terminal_exit_outcome,
    watchdog_decision,
)


class ReplayScrollback:
    """Minimal ScrollbackBuffer stand-in fed by the fixture's `pty_tail` steps."""

    def __init__(self) -> None:
        self.data = b""

    def bytes(self) -> bytes:
        return self.data

    def tail_bytes(self, count: int) -> bytes:
        # The real buffer walks its chunk deque to avoid materializing megabytes;
        # a fixture's screen is one small string, so the slice is the same answer.
        return self.data[-count:] if count > 0 else b""


@dataclass(slots=True)
class VirtualClock:
    monotonic_value: float = 100.0
    wall_value: float = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> float:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += seconds


# Stand-in for a bound transcript. Only its presence is read (by
# `session_is_unwitnessed`); the harness feeds records directly rather than
# through a file.
REPLAY_TRANSCRIPT = Path("replay://transcript")


class ReplaySession:
    """Deterministic Session stand-in that shares the production transition contract.

    State transitions go through swe_mux.session.apply_state_transition — the
    exact code path the live Session uses — with the virtual clock injected, so
    the ledger, arbitration, and proven/inferred classification cannot drift
    between production and the golden corpus.
    """

    def __init__(self, backend: str, clock: VirtualClock | None = None) -> None:
        self.clock = clock or VirtualClock()
        self.record = SessionRecord(
            "replay-session",
            f"{backend}-replay",
            "replay-project",
            backend,
            "native-replay",
            ".",
            f"{backend}.exe",
            [],
            state="idle",
            agent_run_id="run-1",
            parser_status="watching",
            parser_schema_version="2",
        )
        # The observer asks the adapter which conversation-identity rules apply
        # (whether mux named the conversation at spawn). Replay uses the real
        # per-backend values so the corpus cannot drift from production.
        self.adapter = SimpleNamespace(
            name=backend,
            reports_conversation_rollover=backend == "claude",
            assigns_conversation_id=backend == "claude",
        )
        self.state_source_priority = -1
        # Which evidence channels this session has ever had. `session_is_unwitnessed`
        # reads both, and the unwitnessed watchdog pair is licensed by their absence,
        # so the harness has to track them the way a live Session does rather than
        # letting a fixture assert the conclusion directly.
        self.transcript_path: Path | None = None
        self.last_hook_ts = 0.0
        self.last_turn_hook_ts = 0.0
        self.tool_names: dict[str, str] = {}
        self.observation_state: dict[str, Any] = {
            "root_turn_active": False,
            "root_completion_seen": False,
            "codex_scope": "root",
        }
        self.input_owner: str | None = "replay-browser"
        self.subscribers: set[str] = {"replay-browser"}
        self.input_revision = 0
        self.last_input_event_ts = 0.0
        # The daemon's own screen reading, which a live session feeds from the PTY
        # stream. A fixture that says nothing about the screen leaves it unknown,
        # exactly as a session whose switch predates the retained scrollback does.
        self.screen = ScreenModeParser()
        self.bracketed_paste = BracketedPasteParser()
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0
        # The CLI screen this session's PTY would be showing; fixtures drive it
        # with `pty_tail` steps, so the approval veto and the watchdog's resume
        # path are exercised through the same code production uses.
        self.scrollback = ReplayScrollback()
        # Status-contract diagnostics, mirroring Session.
        self.last_state_change_ts = self.clock.wall()
        self.last_state_change_monotonic = self.clock.monotonic()
        self.last_evidence_ts = self.clock.wall()
        self.state_transitions: deque[dict[str, Any]] = deque(
            maxlen=STATE_TRANSITION_LOG_LIMIT
        )
        self.state_changes: deque[dict[str, Any]] = deque(maxlen=STATE_CHANGE_LOG_LIMIT)
        self.status_health_counters: dict[str, int] = {}
        self.watchdog_recovery_actions: dict[str, int] = {}
        self.terminal_latencies: deque[dict[str, Any]] = deque(maxlen=32)
        self.watchdog_recoveries = 0
        # Startup-dialog and classifier-blindness tracking, mirroring Session.
        self.startup_dialog_screen_since: float | None = None
        self.classifier_blind_since: float | None = None
        self.classifier_blind_counted = False
        self.cli_state: dict[str, Any] | None = None
        # Mirrors Session: last observed reading per detection-ladder layer
        # (`note_layer_reading` tracks flips here) and the replay gate the
        # manager-side watchdog pass consults.
        self.layer_readings: dict[str, str] = {}
        self.observation_replay = False

    def publish_update(self) -> None:
        return

    def note_watchdog_recovery(
        self,
        action: str,
        detail: str | None = None,
        *,
        stalled_seconds: float | None = None,
        tail_verdict: str | None = None,
    ) -> None:
        self.watchdog_recoveries += 1
        self.watchdog_recovery_actions[action] = (
            self.watchdog_recovery_actions.get(action, 0) + 1
        )
        self.state_transitions.append(
            {
                "ts": self.clock.wall(),
                "kind": "watchdog_recovery",
                "action": action,
                "detail": detail,
                "stalled_seconds": (
                    round(stalled_seconds, 3) if stalled_seconds is not None else None
                ),
                "tail_verdict": tail_verdict,
            }
        )

    def note_reopen_blocked(self, source: str) -> None:
        counters = self.status_health_counters
        counters["reopen_blocked"] = counters.get("reopen_blocked", 0) + 1
        self.state_transitions.append(
            {
                "ts": self.clock.wall(),
                "kind": "reopen_blocked",
                "source": source,
                "state": self.record.state,
            }
        )

    def status_health(self, now: float | None = None) -> dict[str, Any]:
        return session_status_health(self, now=self.clock.wall() if now is None else now)

    def transition(
        self,
        state: SessionState,
        detail: str | None,
        *,
        source: str,
        evidence: str | None = None,
        inferred: bool | None = None,
        awaiting_reason: str | None = None,
        idle_reason: str | None = None,
        force: bool = False,
    ) -> bool:
        return apply_state_transition(
            self,
            state,
            detail,
            source=source,
            evidence=evidence,
            inferred=inferred,
            awaiting_reason=awaiting_reason,
            idle_reason=idle_reason,
            force=force,
            now=self.clock.wall(),
            monotonic_now=self.clock.monotonic(),
        )


def normalized_standing_activity(session: ReplaySession) -> list[dict[str, Any]]:
    """Golden projection of the annotation set: stable order, display fields only.

    Absolute timestamps stay out so fixtures do not encode the virtual epoch;
    whether an annotation self-expires is projected as a boolean instead.
    """
    return [
        {
            "kind": activity.kind,
            "source": activity.source,
            "evidence": activity.evidence,
            "count": activity.count,
            "detail": activity.detail,
            "expires": activity.expires_at is not None,
        }
        for activity in sorted(session.record.standing_activity, key=lambda a: a.kind)
    ]


def normalized_event(event: MuxEvent) -> dict[str, Any]:
    item: dict[str, Any] = {"type": event.type, "source": event.source}
    for key in ("scope", "kind", "outcome", "tool", "success", "previous", "state"):
        if key in event.payload:
            item[key] = event.payload[key]
    return item


def normalized_state_stream(session: ReplaySession) -> list[dict[str, Any]]:
    """Golden projection of the transition ledger: one entry per visible change."""
    stream: list[dict[str, Any]] = []
    for entry in session.state_transitions:
        if entry.get("kind") != "transition":
            continue
        if entry["previous"] == entry["state"]:
            continue
        item: dict[str, Any] = {
            "previous": entry["previous"],
            "state": entry["state"],
            "source": entry["source"],
            "proof": entry["proof"],
        }
        if entry.get("awaiting_reason"):
            item["awaiting_reason"] = entry["awaiting_reason"]
        stream.append(item)
    return stream


class DetectionReplay:
    def __init__(self, backend: str) -> None:
        self.clock = VirtualClock()
        self.session = ReplaySession(backend, self.clock)
        self.events = EventBus(clock=self.clock.wall)
        self.queue = self.events.subscribe()
        self.readiness = DeliveryReadinessTracker(clock=self.clock.monotonic)
        self.normalized: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.state_checkpoints: list[dict[str, Any]] = []
        self.standing_checkpoints: list[dict[str, Any]] = []
        self.decoder = IncrementalJsonlDecoder()
        # What the transcript file's tail would contain on disk. Regular
        # transcript steps append here too; "transcript_tail" steps append
        # without driving the observer (modeling records the observer missed).
        self.tail_records: list[dict[str, Any]] = []
        self.pty_tail = ""

    async def drain(self) -> None:
        while not self.queue.empty():
            event = await self.queue.get()
            # UI/automation fanout after a user transcript record is consumed is
            # not status evidence. Keep the detection goldens scoped to state,
            # attention, and standing-activity semantics.
            if event.type == "transcript_message":
                continue
            self.normalized.append(normalized_event(event))
            self.readiness.observe(event, self.session)

    def _stamp(self, record: dict[str, Any], offset: float) -> dict[str, Any]:
        """Give a fixture record a virtual-clock timestamp.

        Ordering against the moment an `awaiting` was raised is what licenses
        clearing it, so fixtures express record time relative to "now" instead
        of hard-coding epochs that would drift from the harness clock.
        """
        stamped = dict(record)
        moment = datetime.fromtimestamp(self.clock.wall() + offset, tz=UTC)
        stamped["timestamp"] = moment.isoformat().replace("+00:00", "Z")
        return stamped

    async def transcript_record(self, record: dict[str, Any]) -> None:
        # Observing a record means a transcript is bound, which is what production
        # sets before the observer reads its first line.
        self.session.transcript_path = REPLAY_TRANSCRIPT
        self.tail_records.append(dict(record))
        recognized, signature = classify_transcript_event(self.session.record.backend, record)
        if self.session.record.backend == "claude":
            await _claude(self.session, record, self.events)  # type: ignore[arg-type]
        else:
            await _codex(self.session, record, self.events)  # type: ignore[arg-type]
        await _record_parser_observation(
            self.session, self.events, recognized, signature  # type: ignore[arg-type]
        )

    async def step(self, step: dict[str, Any]) -> None:
        kind = str(step["kind"])
        if kind == "transcript":
            record = dict(step["record"])
            if "ts_offset" in step:
                record = self._stamp(record, float(step["ts_offset"]))
            await self.transcript_record(record)
        elif kind == "transcript_chunk":
            if step.get("truncate"):
                self.decoder.reset()
                self.tail_records = []
            for record in self.decoder.feed(str(step.get("data") or "").encode()):
                await self.transcript_record(record)
        elif kind == "transcript_tail":
            # Records that reached the transcript file but were never observed
            # live (crashed/stuck observer, records lost mid-race). Only the
            # watchdog's tail read can see them.
            self.session.transcript_path = REPLAY_TRANSCRIPT
            for record in step.get("records") or []:
                self.tail_records.append(dict(record))
        elif kind == "pty_tail":
            self.pty_tail = str(step.get("data") or "")
            self.session.scrollback.data = self.pty_tail.encode("utf-8")
        elif kind == "watchdog":
            await self._watchdog_pass()
        elif kind == "catchup":
            await self._catchup(step)
        elif kind == "exit":
            await self._process_exit(step)
        elif kind == "lifecycle":
            # Daemon-owned lifecycle ownership changes (spawn/promotion/demotion)
            # through the same forced transition SessionManager applies.
            self.session.transition(
                str(step["state"]),  # type: ignore[arg-type]
                step.get("detail"),
                source="daemon",
                evidence=str(step.get("evidence") or "lifecycle"),
                force=True,
            )
        elif kind == "hook":
            # A hook reaching this session is what proves the side channel exists,
            # exactly as the live ingress records before dispatching — and, like
            # the ingress, only for this session's own conversation: a nested
            # child's hook is ledgered and dropped without counting as evidence.
            payload = dict(step.get("payload") or {})
            if foreign_conversation_hook_id(self.session, payload) is None:  # type: ignore[arg-type]
                self.session.last_hook_ts = self.clock.wall()
            await apply_hook_observation(
                self.session,  # type: ignore[arg-type]
                str(step["event"]),
                payload,
                self.events,
            )
        elif kind == "terminal":
            # The browser's report of xterm's active buffer. Diagnostic only: it
            # describes the buffer that pane's replayed copy of the stream landed
            # in, which is not always the one the child selected.
            self.session.terminal_mode = str(step["mode"])
            self.session.terminal_mode_updated_at = self.clock.monotonic()
        elif kind == "pty":
            # Raw child output, which is where the daemon reads the screen switch.
            self.session.screen.feed(str(step["data"]).encode("utf-8", "surrogateescape"))
        elif kind == "input":
            self.session.input_revision += 1
            self.session.last_input_event_ts = self.clock.monotonic()
            await self.events.emit(
                "terminal_input",
                session_id=self.session.record.id,
                source="daemon",
                input_owner=True,
                bytes=int(step.get("bytes", 1)),
            )
        elif kind == "terminal_response":
            await self.events.emit(
                "terminal_protocol_response",
                session_id=self.session.record.id,
                source="browser",
                bytes=int(step.get("bytes", 1)),
            )
        elif kind == "focus":
            await self.events.emit(
                "terminal_focus_changed",
                session_id=self.session.record.id,
                source="browser",
                focused=bool(step.get("focused", True)),
            )
        elif kind == "process":
            await self.events.emit(
                "process_observed",
                session_id=self.session.record.id,
                source="process",
                alive=bool(step.get("alive", True)),
                descendants=int(step.get("descendants", 0)),
            )
        elif kind == "timer":
            self.clock.advance(float(step["seconds"]))
        elif kind == "session":
            for key, value in dict(step.get("record") or {}).items():
                setattr(self.session.record, key, value)
            if "terminal_mode" in step:
                self.session.terminal_mode = step["terminal_mode"]
                self.session.terminal_mode_updated_at = self.clock.monotonic()
            if "connected" in step:
                connected = bool(step["connected"])
                self.session.subscribers = {"replay-browser"} if connected else set()
                self.session.input_owner = "replay-browser" if connected else None
        elif kind == "restart":
            self.readiness = DeliveryReadinessTracker(clock=self.clock.monotonic)
        elif kind == "event":
            payload = dict(step.get("payload") or {})
            await self.events.emit(
                str(step["event"]),
                session_id=self.session.record.id,
                source=str(step.get("source") or "fixture"),
                **payload,
            )
        else:
            raise AssertionError(f"unknown replay step: {kind}")
        await self.drain()
        if "expect_delivery" in step:
            actual = self.readiness.evaluate(self.session)
            self.checkpoints.append(
                {
                    "expected": step["expect_delivery"],
                    "actual": actual["delivery_state"],
                    "reason": actual["reason"],
                    "oracle_safe": bool(step.get("oracle_safe", False)),
                }
            )
        if "expect_state" in step or "expect_awaiting" in step:
            self.state_checkpoints.append(
                {
                    "expected_state": step.get("expect_state"),
                    "actual_state": self.session.record.state,
                    "expected_awaiting": step.get("expect_awaiting"),
                    "actual_awaiting": self.session.record.awaiting_reason,
                }
            )
        if "expect_standing" in step:
            self.standing_checkpoints.append(
                {
                    "expected": step["expect_standing"],
                    "actual": normalized_standing_activity(self.session),
                }
            )

    async def _watchdog_pass(self) -> None:
        """One quiescence-watchdog evaluation over the fixture's evidence.

        Reuses the production decision (watchdog_decision), tail classifier
        (tail_turn_state), idle-prompt heuristic (pty_tail_appears_idle), and
        recovery application (apply_watchdog_recovery) so the harness cannot
        drift from _watchdog_check_session.
        """
        session = self.session
        now = self.clock.wall()
        # Mirrors _watchdog_check_session: the annotation TTL sweep runs before
        # any other watchdog rule and owes nothing to turn state.
        expire_standing_activity(session, now=now)
        stalled = max(0.0, self.clock.monotonic() - session.last_state_change_monotonic)
        pty_state = pty_tail_state(self.pty_tail)
        unwitnessed = session_is_unwitnessed(session)
        # Startup-dialog tracking and the classifier-drift self-check mirror the
        # production pass through the same shared helpers.
        if session.record.state in {"idle", "awaiting"}:
            startup_no_turn, dialog_seconds = startup_dialog_observation(
                session, pty_state, now
            )
        else:
            session.startup_dialog_screen_since = None
            startup_no_turn, dialog_seconds = False, None
        if session.record.state in {"working", "awaiting"}:
            note_classifier_blindness(session, pty_state, now)
        else:
            session.classifier_blind_since = None
            session.classifier_blind_counted = False
        startup_raised = bool(session.observation_state.get("startup_dialog_raised"))
        # Mirrors _watchdog_check_session: the unwitnessed pair is evaluated first
        # (it is the only rule that reads an idle session), then the awaiting-resume
        # pass before the transcript tail is even read, because after an approval the
        # transcript is usually busy rather than quiet.
        action = watchdog_decision(
            session.record.state,
            stalled_seconds=stalled,
            tail_verdict=None,
            pty_state=pty_state,
            awaiting_reason=session.record.awaiting_reason,
            unwitnessed=unwitnessed,
            startup_no_turn=startup_no_turn,
            startup_dialog_seconds=dialog_seconds,
            startup_dialog_raised=startup_raised,
        )
        verdict: str | None = None
        if action == "none":
            verdict = tail_turn_state(session.record.backend, self.tail_records)
            action = watchdog_decision(
                session.record.state,
                stalled_seconds=stalled,
                tail_verdict=verdict,
                pty_state=pty_state,
                awaiting_reason=session.record.awaiting_reason,
                unwitnessed=unwitnessed,
                startup_no_turn=startup_no_turn,
                startup_dialog_seconds=dialog_seconds,
                startup_dialog_raised=startup_raised,
            )
        if action == "none":
            return
        await apply_watchdog_recovery(
            session,
            self.events,
            action,
            stalled_seconds=stalled,
            tail_verdict=verdict,
        )

    async def _catchup(self, step: dict[str, Any]) -> None:
        """Resolve observer attach over pre-existing transcript content."""
        session = self.session
        session.observation_state["root_turn_active"] = bool(step.get("open_turn", False))
        historical_seen = int(step.get("historical_seen", 0))
        age = float(step.get("age_seconds", 0.0))
        attach_ts = self.clock.wall()
        last_historical_ts = attach_ts - age if historical_seen else None
        await _finish_transcript_catchup(
            session,  # type: ignore[arg-type]
            self.events,
            attach_ts,
            last_historical_ts,
            historical_seen,
        )

    async def _process_exit(self, step: dict[str, Any]) -> None:
        """PTY root exit through the production outcome mapping and ledger."""
        raw_exit = step.get("exit_code")
        exit_code = int(raw_exit) if raw_exit is not None else None
        state, final_reason, detail = terminal_exit_outcome(
            str(step.get("completion_mode") or "interactive"),
            stopping=bool(step.get("stopping", False)),
            exit_code=exit_code,
            reason=str(step.get("reason") or "process_exit"),
        )
        self.session.record.exit_code = exit_code
        self.session.transition(
            state,
            detail if detail is not None else self.session.record.state_detail,
            source="pty",
            evidence=f"process_exit:{final_reason}",
            force=True,
        )
        await self.events.emit(
            "session_exited" if state == "exited" else "session_crashed",
            session_id=self.session.record.id,
            source="pty",
            reason=final_reason,
            exit_code=exit_code,
        )

    async def run(self, manifest: dict[str, Any]) -> dict[str, Any]:
        for step in manifest["steps"]:
            await self.step(step)
        readiness = self.readiness.evaluate(self.session)
        return {
            "events": [item for item in self.normalized if item["type"] != "state_changed"],
            "states": normalized_state_stream(self.session),
            "checkpoints": self.checkpoints,
            "state_checkpoints": self.state_checkpoints,
            "standing_checkpoints": self.standing_checkpoints,
            "standing_activity": normalized_standing_activity(self.session),
            # Annotation lifecycle actions, for the corpus coverage matrix:
            # every kind must appear with at least one add and one clear.
            "standing_ledger": [
                {"action": entry["action"], "activity": entry["activity"]}
                for entry in self.session.state_transitions
                if entry.get("kind") == "standing_activity"
            ],
            "readiness": readiness,
            "health": {
                "counters": dict(self.session.status_health_counters),
                "watchdog_recoveries": self.session.watchdog_recoveries,
                "watchdog_recovery_actions": dict(self.session.watchdog_recovery_actions),
            },
            "parser": {
                "status": self.session.record.parser_status,
                "recognized": self.session.record.parser_events_seen,
                "unknown": self.session.record.parser_unknown_events,
                "unknown_signatures": self.session.record.parser_unknown_signatures,
            },
        }


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["backend"] in {"claude", "codex"}
    assert isinstance(manifest["steps"], list)
    return manifest
