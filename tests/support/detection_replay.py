from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent, SessionRecord, SessionState
from swe_mux.observation import (
    IncrementalJsonlDecoder,
    _claude,
    _codex,
    _record_parser_observation,
    apply_hook_observation,
    classify_transcript_event,
)


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


class ReplaySession:
    def __init__(self, backend: str) -> None:
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
        self.state_source_priority = -1
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
        self.terminal_mode: str | None = None
        self.terminal_mode_updated_at = 0.0

    def publish_update(self) -> None:
        return

    def transition(self, state: SessionState, detail: str | None, *, source: str) -> bool:
        priority = {"pty": 0, "transcript": 1, "hook": 2}.get(source, 0)
        if priority < self.state_source_priority:
            return False
        changed = self.record.state != state or self.record.state_detail != detail
        self.state_source_priority = priority
        if not changed:
            return False
        self.record.state = state
        self.record.state_detail = detail
        return True


def normalized_event(event: MuxEvent) -> dict[str, Any]:
    item: dict[str, Any] = {"type": event.type, "source": event.source}
    for key in ("scope", "kind", "outcome", "tool", "success", "previous", "state"):
        if key in event.payload:
            item[key] = event.payload[key]
    return item


class DetectionReplay:
    def __init__(self, backend: str) -> None:
        self.clock = VirtualClock()
        self.session = ReplaySession(backend)
        self.events = EventBus(clock=self.clock.wall)
        self.queue = self.events.subscribe()
        self.readiness = DeliveryReadinessTracker(clock=self.clock.monotonic)
        self.normalized: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.decoder = IncrementalJsonlDecoder()

    async def drain(self) -> None:
        while not self.queue.empty():
            event = await self.queue.get()
            self.normalized.append(normalized_event(event))
            self.readiness.observe(event, self.session)

    async def transcript_record(self, record: dict[str, Any]) -> None:
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
            await self.transcript_record(dict(step["record"]))
        elif kind == "transcript_chunk":
            if step.get("truncate"):
                self.decoder.reset()
            for record in self.decoder.feed(str(step.get("data") or "").encode()):
                await self.transcript_record(record)
        elif kind == "hook":
            await apply_hook_observation(
                self.session,  # type: ignore[arg-type]
                str(step["event"]),
                dict(step.get("payload") or {}),
                self.events,
            )
        elif kind == "terminal":
            self.session.terminal_mode = str(step["mode"])
            self.session.terminal_mode_updated_at = self.clock.monotonic()
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

    async def run(self, manifest: dict[str, Any]) -> dict[str, Any]:
        for step in manifest["steps"]:
            await self.step(step)
        readiness = self.readiness.evaluate(self.session)
        return {
            "events": [item for item in self.normalized if item["type"] != "state_changed"],
            "checkpoints": self.checkpoints,
            "readiness": readiness,
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
