from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import logging
import re
import time
import tomllib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .automation_store import AutomationStore
from .background_tasks import background
from .config import Config
from .event_bus import EventBus
from .models import MuxEvent, SessionRecord
from .openrouter import OpenRouterClient, OpenRouterError, OpenRouterResult
from .session import SessionManager
from .transcript_view import parse_transcript_cached

AUTOMATION_INGEST_LOOP = "automation-ingest"
AUTOMATION_WATCH_LOOP = "automation-watch"
AUTOMATION_INTERVAL_LOOP = "automation-intervals"
AUTOMATION_WORKER_LOOP = "automation-worker"

log = logging.getLogger(__name__)

EVENT_SCHEMA_VERSION = 1
MAX_CHAIN_DEPTH = 4
# Cost reconciliation is what makes the dollar budget a bound rather than an
# estimate, so a transient provider error retries instead of dropping the call.
RECONCILE_ATTEMPTS = 3
RECONCILE_BACKOFF_SECONDS = 2.0
# Conservative per-call ceiling used when the model catalog cannot price a model.
# Skipping the dollar preflight entirely (what happened on an empty cache) turns
# the dollar budget off exactly when the daemon knows least.
UNPRICED_CALL_ESTIMATE_USD = 0.50
MAX_EVENT_TEXT = 4096
MAX_SLICE_BYTES = 512 * 1024
MAX_SLICE_MESSAGES = 24
ALLOWED_RULE_FIELDS = {"id", "name", "enabled", "shadow", "on", "when", "do"}
ALLOWED_TRIGGER_OPTIONS = {
    "debounce_s",
    "interval_s",
    "rate_limit_s",
    "quiet_hours",
    "threshold",
    "unless_annotation",
    "annotation_guard_s",
}
ALLOWED_CONDITION_OPERATORS = {
    "eq",
    "ne",
    "in",
    "glob",
    "contains",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
}
CONDITION_SHORTHANDS = {
    "backend",
    "project_scope_id",
    "session_name",
    "project_id",
    "state",
    "attended",
    "context_pct",
    "pinned",
    "source",
    "confidence",
}
ACTION_FIELDS = {
    "annotate": {"kind", "tag", "content"},
    "notify": {"kind", "notification_kind", "title", "message", "severity"},
    "llm": {
        "kind",
        "model",
        "input",
        "prompt",
        "schema",
        "on_result",
        "minimum_capability",
    },
}
SLICE_KINDS = {"last_turn", "last_n_messages", "since_event", "since_annotation", "summary_chain"}
CAPABILITY_RANK = {"telemetry": 1, "inferred": 1, "semantic": 2, "derived": 2, "trusted": 3}
ADAPTER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "claude": {
        "native_hooks": True,
        "transcript": "semantic",
        "pty": "telemetry",
        "normalized_events": [
            "turn_started",
            "turn_ended",
            "tool_use",
            "tool_result",
            "approval_needed",
        ],
    },
    "codex": {
        "native_hooks": True,
        "transcript": "semantic",
        "pty": "telemetry",
        "normalized_events": [
            "turn_started",
            "turn_ended",
            "tool_use",
            "tool_result",
            "approval_needed",
        ],
    },
    "shell": {
        "native_hooks": False,
        "transcript": None,
        "pty": "telemetry",
        "normalized_events": [],
    },
}

# Built-ins execute through the rule engine but are configured as product settings rather
# than rules.toml entries. Keep their user-facing inventory explicit so the control plane
# can show the complete effective setup, including disabled observers.
BUILTIN_OBSERVER_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "builtin.session-titler",
        "name": "Session titler",
        "setting_key": "observer_titler_enabled",
        "setting_label": "Session titler",
        "trigger": "turn_ended",
        "input": "Last completed turn",
        "model": "Cheap model",
        "result": "Run note used as the generated session title",
        "description": "Creates one compact task title for each agent run.",
    },
    {
        "id": "builtin.turn-summarizer",
        "name": "Turn summarizer",
        "setting_key": "observer_summarizer_enabled",
        "setting_label": "Turn summarizer",
        "trigger": "turn_ended",
        "input": "Last completed turn",
        "model": "Cheap model",
        "result": "Run note tagged turn-summary",
        "description": "Records a one-line factual summary after each completed turn.",
    },
    {
        "id": "builtin.stalled-triage",
        "name": "Stalled run triage",
        "setting_key": "phase7_observers_enabled",
        "setting_label": "Attention observers",
        "trigger": "stalled",
        "input": "Recent summary chain",
        "model": "Cheap model",
        "result": "Attention inbox warning",
        "description": "Explains whether a detected stall appears to need user attention.",
    },
    {
        "id": "builtin.approval_needed-triage",
        "name": "Approval request triage",
        "setting_key": "phase7_observers_enabled",
        "setting_label": "Attention observers",
        "trigger": "approval_needed",
        "input": "Last completed turn",
        "model": "Cheap model",
        "result": "Attention inbox warning",
        "description": "Summarizes an approval request without approving or rejecting it.",
    },
    {
        "id": "builtin.context-handoff",
        "name": "Context handoff suggestion",
        "setting_key": "phase7_observers_enabled",
        "setting_label": "Attention observers",
        "trigger": "context_pressure",
        "input": "Last 18 transcript messages",
        "model": "Standard model",
        "result": "Run note tagged handoff-suggestion",
        "description": "Drafts a concise handoff note when context usage is pressured.",
    },
)

EVENT_PAYLOAD_FIELDS: dict[str, set[str]] = {
    "session_spawned": {"backend", "name", "project_scope_id", "repo_group_id"},
    "session_exited": {"reason"},
    "session_crashed": {"reason"},
    "backend_detected": {"backend", "native_session_id"},
    "backend_demoted": {"backend", "native_session_id"},
    "turn_started": {"detail"},
    "turn_ended": {"duration_ms", "detail"},
    "tool_use": {"tool", "detail", "target"},
    "tool_result": {"tool", "success", "exit_code", "detail"},
    "approval_needed": {"kind", "detail"},
    "state_changed": {"previous", "state", "detail"},
    "runtime_cwd_changed": {"dropped"},
    "git_changed": {"branch", "dirty", "ahead", "behind"},
    "process_snapshot": {"cpu_percent", "memory_bytes", "descendants", "listeners"},
    "listener_detected": {"host", "port"},
    "listener_closed": {"host", "port"},
    "process_action": {"pid", "action"},
    "process_ownership_degraded": {"error"},
    "terminal_attached": {"connections"},
    "terminal_detached": {"connections"},
    "terminal_input": {"input_owner", "bytes"},
    "broadcast_delivered": {"targets"},
    # Prompt-queue events carry ids/counters only — never the prompt body.
    "queue_updated": {"message_id", "state", "pending"},
    "queue_delivery": {"message_id", "outcome", "delivery_state", "confirmed", "bytes"},
    "capability_degraded": {"capability", "reason", "minimum"},
    "annotation_created": {"annotation_id", "tag", "rule_id"},
    "notification_created": {"notification_id", "kind"},
    "stalled": {"evidence", "confidence", "subtype"},
    "unattended_attention": {"evidence", "confidence"},
    "runaway": {"evidence", "confidence"},
    "claim_unverified": {"evidence", "confidence"},
    "context_pressure": {"evidence", "confidence", "context_pct"},
    "environment_interlock": {"evidence", "confidence", "kind", "sessions"},
    "attention_digest_due": {"since", "items"},
    "timer": {"rule_id", "interval_s"},
    "hook_reload_failed": {"error"},
    "hook_rules_reloaded": {"rules"},
    "hook_action_failed": {"rule", "error"},
    "usage_refreshed": {"provider"},
    "usage_refresh_failed": {"provider", "error"},
    "configuration_changed": {"revision"},
    "configuration_error": {"error"},
}

SOURCE_CONFIDENCE = {
    "hook": ("native_hook", 1.0, "semantic"),
    "transcript": ("transcript", 0.9, "semantic"),
    "daemon": ("mux", 1.0, "trusted"),
    "settings": ("mux", 1.0, "trusted"),
    "user": ("mux", 1.0, "trusted"),
    "pty": ("pty", 0.45, "telemetry"),
    "process": ("process", 0.8, "telemetry"),
    "git": ("git", 0.9, "telemetry"),
    "automation": ("automation", 1.0, "derived"),
    "hooks": ("legacy_hook", 0.9, "semantic"),
    "ccusage": ("usage", 0.9, "telemetry"),
    "external_file": ("settings", 1.0, "trusted"),
}

OBSERVER_SCHEMAS: dict[str, dict[str, Any]] = {
    "title_v1": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "confidence"],
        "properties": {
            "title": {"type": "string", "maxLength": 80},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
    "summary_v1": {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "confidence"],
        "properties": {
            "summary": {"type": "string", "maxLength": 320},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
    "attention_v1": {
        "type": "object",
        "additionalProperties": False,
        "required": ["needs_attention", "summary", "confidence"],
        "properties": {
            "needs_attention": {"type": "boolean"},
            "summary": {"type": "string", "maxLength": 320},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
    "experience_v1": {
        "type": "object",
        "additionalProperties": False,
        "required": ["error", "resolution", "confidence"],
        "properties": {
            "error": {"type": "string", "maxLength": 400},
            "resolution": {"type": "string", "maxLength": 800},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
}


class RuleValidationError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class NormalizedEvent:
    version: int
    seq: int
    ts: float
    type: str
    session_id: str | None
    agent_run_id: str | None
    backend: str | None
    project_scope_id: str | None
    session_name: str | None
    project_id: str | None
    state: str | None
    attended: bool
    context_pct: float
    pinned: bool
    source: str
    confidence: float
    capability: str
    chain_id: str
    chain_depth: int
    payload: dict[str, Any]
    chain_rules: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class Rule:
    id: str
    name: str
    enabled: bool
    shadow: bool
    trigger: str
    trigger_options: dict[str, Any]
    conditions: tuple[dict[str, Any], ...]
    actions: tuple[dict[str, Any], ...]
    revision: str
    source: str = "canonical"

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class TranscriptSlice:
    kind: str
    messages: tuple[dict[str, Any], ...]
    bytes: int
    estimated_tokens: int
    truncated: bool
    input_hash: str

    def render(self) -> str:
        rows: list[str] = []
        for message in self.messages:
            content: list[str] = []
            for block in message.get("content") or []:
                if block.get("type") == "text":
                    content.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_use":
                    content.append(f"[tool {block.get('name') or 'unknown'}]")
            rows.append(f"{message.get('role', 'unknown')}: {' '.join(content)}")
        return "\n".join(rows)


def normalize_event(
    event: MuxEvent, record: SessionRecord | None, *, attended: bool = False
) -> NormalizedEvent:
    source, confidence, capability = SOURCE_CONFIDENCE.get(
        event.source, ("adapter", 0.7, "inferred")
    )
    allowed = EVENT_PAYLOAD_FIELDS.get(event.type, set())
    payload = {key: _bounded_value(value) for key, value in event.payload.items() if key in allowed}
    chain_id = str(event.payload.get("_chain_id") or uuid.uuid4())
    try:
        chain_depth = int(event.payload.get("_chain_depth") or 0)
    except (TypeError, ValueError):
        chain_depth = 0
    raw_chain_rules = event.payload.get("_chain_rules")
    chain_rules = tuple(
        str(item)[:80]
        for item in (raw_chain_rules if isinstance(raw_chain_rules, list) else [])[:MAX_CHAIN_DEPTH]
    )
    return NormalizedEvent(
        version=EVENT_SCHEMA_VERSION,
        seq=event.seq,
        ts=event.ts,
        type=event.type,
        session_id=event.session_id,
        agent_run_id=record.agent_run_id if record else None,
        backend=record.backend if record else None,
        project_scope_id=record.trusted_scope_id if record else None,
        session_name=record.name if record else None,
        project_id=record.project_id if record else None,
        state=record.state if record else None,
        attended=attended,
        context_pct=record.context_pct if record else 0,
        pinned=record.pinned_attention if record else False,
        source=source,
        confidence=confidence,
        capability=capability,
        chain_id=chain_id,
        chain_depth=chain_depth,
        payload=payload,
        chain_rules=chain_rules,
    )


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_EVENT_TEXT]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, list):
        return [_bounded_value(item) for item in value[:32]]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded_value(item) for key, item in list(value.items())[:32]}
    return str(value)[:MAX_EVENT_TEXT]


def parse_rules(text: str, *, source: str = "canonical") -> list[Rule]:
    try:
        document = tomllib.loads(text or "version = 1\n")
    except tomllib.TOMLDecodeError as exc:
        raise RuleValidationError(str(exc)) from exc
    if int(document.get("version", 1)) != 1:
        raise RuleValidationError("unsupported rules schema version")
    raw_rules = document.get("rule", [])
    if not isinstance(raw_rules, list):
        raise RuleValidationError("rule must be an array of tables")
    result: list[Rule] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise RuleValidationError(f"rule {index} must be a table")
        unknown_fields = set(raw) - ALLOWED_RULE_FIELDS
        if unknown_fields:
            raise RuleValidationError(
                f"rule {index} has unknown fields: {', '.join(sorted(unknown_fields))}"
            )
        rule_id = str(raw.get("id") or "").strip()
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", rule_id):
            raise RuleValidationError(f"rule {index} has an invalid id")
        if rule_id in seen:
            raise RuleValidationError(f"duplicate rule id: {rule_id}")
        seen.add(rule_id)
        on = raw.get("on")
        if isinstance(on, str):
            trigger, trigger_options = on, {}
        elif isinstance(on, dict):
            trigger = str(on.get("trigger") or "")
            trigger_options = {key: value for key, value in on.items() if key != "trigger"}
        else:
            raise RuleValidationError(f"rule {rule_id} requires on")
        if trigger not in EVENT_PAYLOAD_FIELDS:
            raise RuleValidationError(f"rule {rule_id} uses unknown trigger {trigger!r}")
        unknown_options = set(trigger_options) - ALLOWED_TRIGGER_OPTIONS
        if unknown_options:
            raise RuleValidationError(
                f"rule {rule_id} has unknown trigger options: {', '.join(sorted(unknown_options))}"
            )
        _validate_trigger_options(rule_id, trigger, trigger_options)
        conditions = raw.get("when", [])
        actions = raw.get("do", [])
        if not isinstance(conditions, list) or not all(
            isinstance(item, dict) for item in conditions
        ):
            raise RuleValidationError(f"rule {rule_id} when must be an array of tables")
        if not isinstance(actions, list) or not actions:
            raise RuleValidationError(f"rule {rule_id} requires at least one action")
        for condition in conditions:
            _validate_condition(rule_id, condition)
        for action in actions:
            if not isinstance(action, dict) or action.get("kind") not in {
                "annotate",
                "notify",
                "llm",
            }:
                raise RuleValidationError(
                    f"rule {rule_id} actions are limited to annotate, notify, and llm"
                )
            _validate_action(rule_id, action)
            if action["kind"] == "llm":
                if action.get("schema") not in OBSERVER_SCHEMAS:
                    raise RuleValidationError(f"rule {rule_id} uses an unknown observer schema")
                on_result = action.get("on_result")
                if not isinstance(on_result, dict) or on_result.get("kind") not in {
                    "annotate",
                    "notify",
                }:
                    raise RuleValidationError(
                        f"rule {rule_id} llm on_result must be annotate or notify"
                    )
                _validate_action(rule_id, on_result, result_mapping=True)
        canonical = {
            "id": rule_id,
            "name": str(raw.get("name") or rule_id),
            "enabled": bool(raw.get("enabled", True)),
            "shadow": bool(raw.get("shadow", False)),
            "on": {"trigger": trigger, **trigger_options},
            "when": conditions,
            "do": actions,
        }
        revision = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:20]
        result.append(
            Rule(
                rule_id,
                str(canonical["name"]),
                bool(canonical["enabled"]),
                bool(canonical["shadow"]),
                trigger,
                trigger_options,
                tuple(conditions),
                tuple(actions),
                revision,
                source,
            )
        )
    return result


def _bounded_number(rule_id: str, name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleValidationError(f"rule {rule_id} {name} must be numeric")
    number = float(value)
    if not minimum <= number <= maximum:
        raise RuleValidationError(
            f"rule {rule_id} {name} must be between {minimum:g} and {maximum:g}"
        )
    return number


def _validate_trigger_options(rule_id: str, trigger: str, options: dict[str, Any]) -> None:
    for name in ("debounce_s", "rate_limit_s", "annotation_guard_s"):
        if name in options:
            _bounded_number(rule_id, name, options[name], 0, 86_400)
    if "interval_s" in options:
        _bounded_number(rule_id, "interval_s", options["interval_s"], 5, 86_400)
    if trigger == "timer" and "interval_s" not in options:
        raise RuleValidationError(f"rule {rule_id} timer trigger requires interval_s")
    quiet = options.get("quiet_hours")
    if quiet is not None and (
        not isinstance(quiet, list)
        or len(quiet) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in quiet)
        or any(item < 0 or item > 23 for item in quiet)
    ):
        raise RuleValidationError(f"rule {rule_id} quiet_hours must be two hours from 0 to 23")
    threshold = options.get("threshold")
    if threshold is not None:
        if not isinstance(threshold, dict):
            raise RuleValidationError(f"rule {rule_id} threshold must be a table")
        if set(threshold) - {"field", "op", "value", "hysteresis"}:
            raise RuleValidationError(f"rule {rule_id} threshold has unknown fields")
        if not str(threshold.get("field") or ""):
            raise RuleValidationError(f"rule {rule_id} threshold requires field")
        if str(threshold.get("op") or "gte") not in {"gt", "gte", "lt", "lte"}:
            raise RuleValidationError(f"rule {rule_id} threshold has an invalid operator")
        _bounded_number(rule_id, "threshold value", threshold.get("value"), -1e15, 1e15)
        _bounded_number(rule_id, "threshold hysteresis", threshold.get("hysteresis", 0), 0, 1e15)
    if "unless_annotation" in options and not str(options["unless_annotation"]).strip():
        raise RuleValidationError(f"rule {rule_id} unless_annotation must not be empty")


def _validate_condition(rule_id: str, condition: dict[str, Any]) -> None:
    unknown = set(condition) - ({"field", "op", "value", "in"} | CONDITION_SHORTHANDS)
    if unknown:
        raise RuleValidationError(
            f"rule {rule_id} condition has unknown fields: {', '.join(sorted(unknown))}"
        )
    operator = str(condition.get("op") or ("in" if "in" in condition else "eq"))
    if operator not in ALLOWED_CONDITION_OPERATORS:
        raise RuleValidationError(f"rule {rule_id} condition uses invalid operator {operator!r}")
    field = str(condition.get("field") or "")
    shorthand = set(condition) & CONDITION_SHORTHANDS
    if (bool(field) and shorthand) or (not field and len(shorthand) != 1):
        raise RuleValidationError(f"rule {rule_id} condition requires one field")
    if operator == "in" and not isinstance(condition.get("value", condition.get("in")), list):
        raise RuleValidationError(f"rule {rule_id} in condition requires a list")


def _validate_action(rule_id: str, action: dict[str, Any], *, result_mapping: bool = False) -> None:
    kind = str(action.get("kind") or "")
    unknown = set(action) - ACTION_FIELDS.get(kind, set())
    if unknown:
        label = "llm on_result" if result_mapping else "action"
        raise RuleValidationError(
            f"rule {rule_id} {label} has unknown fields: {', '.join(sorted(unknown))}"
        )
    if kind == "annotate" and not str(action.get("content") or ""):
        raise RuleValidationError(f"rule {rule_id} annotate action requires content")
    if kind == "notify" and not str(action.get("message") or ""):
        raise RuleValidationError(f"rule {rule_id} notify action requires message")
    if kind == "notify" and str(action.get("severity") or "info") not in {
        "info",
        "warning",
        "error",
    }:
        raise RuleValidationError(f"rule {rule_id} notify severity is invalid")
    if kind != "llm":
        return
    input_spec = action.get("input") or {}
    if not isinstance(input_spec, dict):
        raise RuleValidationError(f"rule {rule_id} llm input must be a table")
    if set(input_spec) - {"slice", "messages", "since_ts", "tag"}:
        raise RuleValidationError(f"rule {rule_id} llm input has unknown fields")
    if str(input_spec.get("slice") or "last_turn") not in SLICE_KINDS:
        raise RuleValidationError(f"rule {rule_id} llm input uses an unknown slice")
    if "messages" in input_spec:
        _bounded_number(
            rule_id, "llm input messages", input_spec["messages"], 1, MAX_SLICE_MESSAGES
        )
    minimum = str(action.get("minimum_capability") or "semantic")
    if minimum not in {"telemetry", "semantic", "trusted"}:
        raise RuleValidationError(f"rule {rule_id} llm minimum_capability is invalid")


class TranscriptSliceService:
    async def build(
        self,
        path: Path,
        backend: str,
        kind: str,
        *,
        max_messages: int = MAX_SLICE_MESSAGES,
        max_bytes: int = MAX_SLICE_BYTES,
        since_ts: float | None = None,
    ) -> TranscriptSlice:
        started = time.monotonic()
        messages = await asyncio.wait_for(
            asyncio.to_thread(parse_transcript_cached, path, backend, max_bytes=max_bytes),
            timeout=2,
        )
        if since_ts is not None:
            filtered: list[dict[str, Any]] = []
            for item in messages:
                timestamp = _timestamp(item.get("ts"))
                if timestamp is None or timestamp >= since_ts:
                    filtered.append(item)
            messages = filtered
        if kind == "last_turn":
            start = max(
                (index for index, item in enumerate(messages) if item.get("role") == "user"),
                default=max(0, len(messages) - 2),
            )
            selected = messages[start:]
        elif kind in {"last_n_messages", "since_event", "since_annotation"}:
            selected = messages[-max_messages:]
        else:
            raise ValueError(f"unsupported transcript slice: {kind}")
        selected = selected[-max_messages:]
        encoded = json.dumps(selected, separators=(",", ":"), ensure_ascii=False).encode()
        while selected and len(encoded) > max_bytes:
            selected = selected[1:]
            encoded = json.dumps(selected, separators=(",", ":"), ensure_ascii=False).encode()
        if time.monotonic() - started > 2.1:
            raise TimeoutError("transcript slice exceeded its parsing budget")
        return TranscriptSlice(
            kind,
            tuple(selected),
            len(encoded),
            max(1, len(encoded) // 4),
            len(selected) < len(messages),
            hashlib.sha256(encoded).hexdigest(),
        )

    @staticmethod
    def from_annotations(
        items: list[dict[str, Any]], kind: str = "summary_chain"
    ) -> TranscriptSlice:
        messages = tuple(
            {
                "role": "assistant",
                "ts": item["created_at"],
                "content": [{"type": "text", "text": item["content"]}],
            }
            for item in reversed(items[-24:])
        )
        encoded = json.dumps(messages, separators=(",", ":"), ensure_ascii=False).encode()
        return TranscriptSlice(
            kind,
            messages,
            len(encoded),
            max(1, len(encoded) // 4),
            len(items) > len(messages),
            hashlib.sha256(encoded).hexdigest(),
        )


class AutomationEngine:
    def __init__(
        self,
        path: Path,
        events: EventBus,
        sessions: SessionManager,
        store: AutomationStore,
        config: Config,
        provider: OpenRouterClient,
    ) -> None:
        self.path = path
        self.events = events
        self.sessions = sessions
        self.store = store
        self.config = config
        self.provider = provider
        self.rules: list[Rule] = []
        self._builtin_rule_cache: dict[tuple[str, bool, bool, bool], list[Rule]] = {}
        self.diagnostic: str | None = None
        self.last_loaded_at: float | None = None
        self.queue: asyncio.Queue[NormalizedEvent] = asyncio.Queue(
            maxsize=config.automation_queue_size
        )
        self.queue_dropped = 0
        self.loop_rejections = 0
        # Worker failures are distinct from rules-file diagnostics: they must not
        # be misread as a rules problem, and must not vanish on the next reload.
        self.worker_failures = 0
        self.worker_last_error: str | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._loop_names: list[str] = []
        self._event_queue: asyncio.Queue[MuxEvent] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self._debounce_tasks: dict[tuple[str, str], asyncio.Task[Any]] = {}
        # Unique built-ins (currently the one title per agent run) need an
        # in-process reservation as well as the durable annotation guard. With
        # multiple workers, two evidence sources can otherwise both pass the DB
        # check before either paid call creates its annotation.
        self._unique_inflight: set[tuple[str, str]] = set()
        self._interval_next: dict[str, float] = {}
        self._source_probes: dict[str, dict[str, Any]] = {}
        # A dropped cost reconcile leaves the ledger under-counting that call, so
        # the dollar budget loosens invisibly. Counted and reported, not silent.
        self._unreconciled_calls = 0
        self._last_reconcile_error: str | None = None
        self._mtime = 0
        self.slices = TranscriptSliceService()

    def forget_session(self, session_id: str) -> None:
        """Drop per-session accumulators when a session goes away.

        These are small per session but the daemon is designed to run for weeks
        behind the PTY supervisor, so "one entry per session ever spawned" is an
        unbounded growth path, and stale probes skew the source-health view.
        """
        self._source_probes.pop(session_id, None)

    def start(self) -> None:
        if self._tasks or self._loop_names:
            return
        self.reload()
        self._event_queue = self.events.subscribe(name="automation")
        # Supervised: rule hot reload, event ingest and timer triggers each used to
        # die permanently on their first exception (a TOCTOU stat race is enough).
        for name, factory in (
            (AUTOMATION_INGEST_LOOP, self._ingest),
            (AUTOMATION_WATCH_LOOP, self._watch),
            (AUTOMATION_INTERVAL_LOOP, self._intervals),
        ):
            self._loop_names.append(name)
            self._tasks.append(background.start(name, factory))
        for index in range(self.config.automation_concurrency):
            name = f"{AUTOMATION_WORKER_LOOP}-{index}"
            self._loop_names.append(name)
            self._tasks.append(background.start(name, self._worker_factory(name)))

    def _worker_factory(self, name: str) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            await self._worker(name)

        return run

    async def stop(self) -> None:
        if self._event_queue:
            self.events.unsubscribe(self._event_queue)
        for name in self._loop_names:
            await background.stop(name)
        self._loop_names.clear()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        self._background.clear()
        await self.provider.close()

    def reload(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8") if self.path.exists() else "version = 1\n"
            rules = parse_rules(text)
        except (OSError, RuleValidationError) as exc:
            self.diagnostic = str(exc)
            return
        self.rules = rules
        self.diagnostic = None
        self.last_loaded_at = time.time()
        try:
            self._mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime = 0

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(1)
            with background.iteration(AUTOMATION_WATCH_LOOP):
                # A delete+rename save (every editor does this) makes exists()
                # and stat() disagree; treating that as "unchanged" costs one
                # poll instead of hot reload for the daemon's lifetime.
                try:
                    current = self.path.stat().st_mtime_ns
                except OSError:
                    current = self._mtime if self.path.exists() else 0
                if current != self._mtime:
                    self.reload()

    async def _ingest(self) -> None:
        assert self._event_queue is not None
        while True:
            event = await self._event_queue.get()
            try:
                with background.iteration(AUTOMATION_INGEST_LOOP):
                    await self._ingest_one(event)
            finally:
                self._event_queue.task_done()

    async def _ingest_one(self, event: MuxEvent) -> None:
        session = self.sessions.sessions.get(event.session_id or "")
        if session:
            await self._probe_sources(event, session.record)
        normalized = normalize_event(
            event,
            session.record if session else None,
            attended=bool(session and session.subscribers),
        )
        if normalized.chain_depth > MAX_CHAIN_DEPTH:
            return
        try:
            self.queue.put_nowait(normalized)
        except asyncio.QueueFull:
            self.queue_dropped += 1

    async def _probe_sources(self, event: MuxEvent, record: SessionRecord) -> None:
        capabilities = ADAPTER_CAPABILITIES.get(record.backend, {})
        if not capabilities.get("native_hooks"):
            return
        probe = self._source_probes.setdefault(
            record.id,
            {
                "first_transcript_at": None,
                "transcript_events": 0,
                "last_hook_at": None,
                "degraded": False,
            },
        )
        if event.source == "hook":
            probe["last_hook_at"] = event.ts
            probe["degraded"] = False
            return
        if event.source != "transcript" or event.type not in {
            "turn_started",
            "turn_ended",
            "tool_use",
            "tool_result",
            "approval_needed",
        }:
            return
        probe["first_transcript_at"] = probe["first_transcript_at"] or event.ts
        probe["transcript_events"] += 1
        first = float(probe["first_transcript_at"])
        last_hook = float(probe["last_hook_at"] or 0)
        if (
            not probe["degraded"]
            and int(probe["transcript_events"]) >= 3
            and event.ts - first >= 30
            and last_hook < first
        ):
            probe["degraded"] = True
            await self.events.emit(
                "capability_degraded",
                session_id=record.id,
                source="daemon",
                capability="native_hook",
                minimum="semantic",
                reason=(
                    "semantic transcript activity continued without an expected native hook; "
                    "observation remains transcript-derived"
                ),
            )

    def note_native_hook(self, session_id: str, ts: float | None = None) -> None:
        """Record validated hook ingress even when EventBus semantic dedupe suppresses it."""
        probe = self._source_probes.setdefault(
            session_id,
            {
                "first_transcript_at": None,
                "transcript_events": 0,
                "last_hook_at": None,
                "degraded": False,
            },
        )
        probe["last_hook_at"] = ts if ts is not None else time.time()
        probe["degraded"] = False

    async def _worker(self, loop_name: str) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self.evaluate(event)
            except Exception as exc:  # Automation must never terminate its worker.
                # Worker faults get their own counter and last-error field: they
                # used to share the single `diagnostic` slot with rules-file
                # errors and were cleared by the next reload.
                self.worker_failures += 1
                self.worker_last_error = f"{type(exc).__name__}: {exc}"[:400]
                background.note_fault(loop_name, exc)
            else:
                background.note_progress(loop_name)
            finally:
                self.queue.task_done()

    async def _intervals(self) -> None:
        while True:
            await asyncio.sleep(1)
            with background.iteration(AUTOMATION_INTERVAL_LOOP):
                await self._fire_due_intervals()

    async def _fire_due_intervals(self) -> None:
        now = time.time()
        for rule in self.rules:
            interval = float(rule.trigger_options.get("interval_s") or 0)
            if not rule.enabled or rule.trigger != "timer" or interval < 5:
                continue
            due = self._interval_next.setdefault(rule.id, now + interval)
            if now < due:
                continue
            self._interval_next[rule.id] = now + interval
            await self.events.emit(
                "timer",
                source="automation",
                rule_id=rule.id,
                interval_s=interval,
            )

    async def evaluate(
        self,
        event: NormalizedEvent,
        *,
        rules: list[Rule] | None = None,
        dry_run: bool = False,
        debounced: bool = False,
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        if not self.config.automation_enabled and not dry_run:
            return reports
        candidates = list(rules if rules is not None else self.rules)
        candidates.extend(self._builtin_rules(event))
        for rule in candidates:
            if not rule.enabled or rule.trigger != event.type:
                continue
            if rule.id in event.chain_rules:
                self.loop_rejections += 1
                reports.append(
                    {
                        "rule": rule.snapshot(),
                        "matched": False,
                        "trace": [],
                        "actions": [],
                        "loop_detected": True,
                        "error": f"chain {event.chain_id} revisited rule {rule.id}",
                    }
                )
                continue
            trace = [self._condition(condition, event) for condition in rule.conditions]
            matched = all(row["matched"] for row in trace)
            report: dict[str, Any] = {
                "rule": rule.snapshot(),
                "matched": matched,
                "trace": trace,
                "actions": [],
            }
            reports.append(report)
            if not matched:
                continue
            debounce = float(rule.trigger_options.get("debounce_s") or 0)
            if debounce > 0 and self._tasks and not dry_run and not debounced:
                self._schedule_debounce(rule, event, debounce)
                report["debounced"] = True
                continue
            if not await self._guards_pass(rule, event, dry_run=dry_run):
                report["guarded"] = True
                continue
            if dry_run:
                report["actions"] = [{"would_execute": action} for action in rule.actions]
                continue
            unique_key = self._unique_guard_key(rule, event)
            try:
                firing_id = await self.store.create_firing(
                    event_seq=event.seq,
                    event_type=event.type,
                    agent_run_id=event.agent_run_id,
                    session_id=event.session_id,
                    rule_id=rule.id,
                    rule_revision=rule.revision,
                    chain_id=event.chain_id,
                    chain_depth=event.chain_depth,
                    shadow=rule.shadow or dry_run,
                    trace=trace,
                )
                if not firing_id:
                    report["duplicate"] = True
                    continue
                try:
                    for index, action in enumerate(rule.actions):
                        if rule.shadow:
                            detail = {"would_execute": action}
                            report["actions"].append(detail)
                            await self.store.action_result(
                                firing_id, index, str(action["kind"]), "shadow", detail
                            )
                            continue
                        result = await self._action(firing_id, index, rule, event, action)
                        report["actions"].append(result)
                    await self.store.finish_firing(
                        firing_id, "shadow" if rule.shadow else "completed"
                    )
                    await self._set_guard_checkpoint(rule, event)
                except asyncio.CancelledError:
                    await self.store.finish_firing(firing_id, "cancelled", "cancelled")
                    raise
                except Exception as exc:
                    await self.store.finish_firing(firing_id, "failed", str(exc)[:1000])
                    report["error"] = str(exc)
            finally:
                if unique_key:
                    self._unique_inflight.discard(unique_key)
        return reports

    def _schedule_debounce(self, rule: Rule, event: NormalizedEvent, delay: float) -> None:
        key = (rule.id, event.agent_run_id or event.session_id or "global")
        previous = self._debounce_tasks.get(key)
        if previous and not previous.done():
            previous.cancel()

        async def later() -> None:
            checkpoint_key = f"debounce:{key[0]}:{key[1]}"
            try:
                await self.store.set_checkpoint(
                    checkpoint_key,
                    {
                        "status": "pending",
                        "event_seq": event.seq,
                        "due_at": time.time() + min(delay, 3600),
                    },
                )
                await asyncio.sleep(min(delay, 3600))
                await self.evaluate(event, rules=[rule], debounced=True)
                await self.store.set_checkpoint(
                    checkpoint_key,
                    {"status": "fired", "event_seq": event.seq, "fired_at": time.time()},
                )
            except asyncio.CancelledError:
                await self.store.set_checkpoint(
                    checkpoint_key,
                    {"status": "coalesced", "event_seq": event.seq, "updated_at": time.time()},
                )
                raise
            finally:
                if self._debounce_tasks.get(key) is asyncio.current_task():
                    self._debounce_tasks.pop(key, None)

        task = asyncio.create_task(later(), name=f"automation-debounce-{rule.id}")
        self._debounce_tasks[key] = task
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _condition(self, condition: dict[str, Any], event: NormalizedEvent) -> dict[str, Any]:
        field = str(condition.get("field") or "")
        if not field:
            field = next(
                (
                    key
                    for key in (
                        "backend",
                        "project_scope_id",
                        "session_name",
                        "project_id",
                        "state",
                        "attended",
                        "context_pct",
                        "pinned",
                        "source",
                        "confidence",
                    )
                    if key in condition
                ),
                "",
            )
        value = _field(event.snapshot(), field)
        operator = str(condition.get("op") or ("in" if "in" in condition else "eq"))
        expected = condition.get("value", condition.get("in", condition.get(field)))
        matched = False
        try:
            if operator == "eq":
                matched = value == expected
            elif operator == "ne":
                matched = value != expected
            elif operator == "in":
                matched = isinstance(expected, (list, tuple, set)) and value in expected
            elif operator == "glob":
                matched = fnmatch.fnmatch(str(value or ""), str(expected or ""))
            elif operator == "contains":
                matched = str(expected or "") in str(value or "")
            elif operator in {"gt", "gte", "lt", "lte"}:
                matched = {
                    "gt": value > expected,
                    "gte": value >= expected,
                    "lt": value < expected,
                    "lte": value <= expected,
                }[operator]
            elif operator == "exists":
                matched = (value is not None) == bool(expected)
        except (TypeError, ValueError):
            matched = False
        return {
            "field": field,
            "operator": operator,
            "expected": expected,
            "actual": value,
            "matched": matched,
        }

    async def _guards_pass(
        self, rule: Rule, event: NormalizedEvent, *, dry_run: bool = False
    ) -> bool:
        options = rule.trigger_options
        if rule.trigger == "timer" and event.payload.get("rule_id") != rule.id:
            return False
        checkpoint_key = self._rule_checkpoint_key(rule, event)
        checkpoint = await self.store.checkpoint(checkpoint_key)
        now = time.time()
        rate = float(options.get("rate_limit_s") or 0)
        if checkpoint and now - float(checkpoint.get("fired_at") or 0) < rate:
            return False
        quiet = options.get("quiet_hours")
        if isinstance(quiet, list) and len(quiet) == 2:
            start, end = int(quiet[0]) % 24, int(quiet[1]) % 24
            hour = time.localtime(now).tm_hour
            in_quiet = start <= hour < end if start < end else hour >= start or hour < end
            if in_quiet:
                return False
        threshold = options.get("threshold")
        if isinstance(threshold, dict):
            value = _field(event.snapshot(), str(threshold.get("field") or ""))
            target = threshold.get("value")
            if not isinstance(value, (int, float)) or not isinstance(target, (int, float)):
                return False
            hysteresis = float(threshold.get("hysteresis") or 0)
            armed = bool((checkpoint or {}).get("threshold_armed", True))
            operator = str(threshold.get("op") or "gte")
            crossing = value >= target if operator in {"gt", "gte"} else value <= target
            reset = (
                value <= target - hysteresis
                if operator in {"gt", "gte"}
                else value >= target + hysteresis
            )
            if not armed:
                if reset and not dry_run:
                    await self.store.set_checkpoint(
                        checkpoint_key,
                        {**(checkpoint or {}), "threshold_armed": True},
                    )
                return False
            if not crossing:
                return False
        # The unique reservation is taken last, after every remaining guard has
        # passed. Taking it earlier leaked the key on any later early return (a
        # user-authored rule reusing the builtin titler id plus `unless_annotation`
        # is enough), and the leaked key then blocked that run's title forever.
        unique_key: tuple[str, str] | None = None
        if rule.id == "builtin.session-titler" and event.agent_run_id:
            if await self.store.recent_annotation(event.agent_run_id, "title", 0):
                return False
            unique_key = self._unique_guard_key(rule, event)
            if unique_key in self._unique_inflight:
                return False
        if event.agent_run_id and options.get("unless_annotation"):
            recent = await self.store.recent_annotation(
                event.agent_run_id,
                str(options["unless_annotation"]),
                now - float(options.get("annotation_guard_s") or 600),
            )
            if recent:
                return False
        if unique_key and not dry_run:
            self._unique_inflight.add(unique_key)
        return True

    async def _set_guard_checkpoint(self, rule: Rule, event: NormalizedEvent) -> None:
        threshold = rule.trigger_options.get("threshold")
        await self.store.set_checkpoint(
            self._rule_checkpoint_key(rule, event),
            {
                "fired_at": time.time(),
                **({"threshold_armed": False} if isinstance(threshold, dict) else {}),
            },
        )

    @staticmethod
    def _rule_checkpoint_key(rule: Rule, event: NormalizedEvent) -> str:
        owner = event.agent_run_id or event.session_id or "global"
        return f"rule:{rule.id}:{owner}"

    @staticmethod
    def _unique_guard_key(rule: Rule, event: NormalizedEvent) -> tuple[str, str] | None:
        if rule.id == "builtin.session-titler" and event.agent_run_id:
            return rule.id, event.agent_run_id
        return None

    async def _action(
        self,
        firing_id: str,
        index: int,
        rule: Rule,
        event: NormalizedEvent,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        kind = str(action["kind"])
        try:
            if kind == "annotate":
                if not event.agent_run_id:
                    raise ValueError("annotations require a durable live agent run")
                content = _template(str(action.get("content") or ""), event.snapshot(), None)
                annotation = await self.store.create_annotation(
                    agent_run_id=event.agent_run_id,
                    session_id=event.session_id,
                    tag=str(action.get("tag") or "note")[:80],
                    content=content[:4000],
                    source_event_seq=event.seq,
                    rule_id=rule.id,
                    rule_revision=rule.revision,
                    provenance="rule",
                )
                await self.events.emit(
                    "annotation_created",
                    session_id=event.session_id,
                    source="automation",
                    annotation_id=annotation["id"],
                    tag=annotation["tag"],
                    rule_id=rule.id,
                    _chain_id=event.chain_id,
                    _chain_depth=event.chain_depth + 1,
                    _chain_rules=[*event.chain_rules, rule.id],
                )
                result = {"annotation_id": annotation["id"], "tag": annotation["tag"]}
            elif kind == "notify":
                notification = await self.store.notify(
                    agent_run_id=event.agent_run_id,
                    session_id=event.session_id,
                    rule_id=rule.id,
                    kind=str(action.get("notification_kind") or "rule"),
                    title=_template(str(action.get("title") or rule.name), event.snapshot(), None)[
                        :160
                    ],
                    message=_template(str(action.get("message") or ""), event.snapshot(), None)[
                        :2000
                    ],
                    severity=str(action.get("severity") or "info"),
                )
                await self.events.emit(
                    "notification_created",
                    session_id=event.session_id,
                    source="automation",
                    notification_id=notification["id"],
                    kind=notification["kind"],
                    _chain_id=event.chain_id,
                    _chain_depth=event.chain_depth + 1,
                    _chain_rules=[*event.chain_rules, rule.id],
                )
                result = {"notification_id": notification["id"]}
            elif kind == "llm":
                result = await self._llm(firing_id, rule, event, action)
            else:
                raise ValueError(f"unsupported action: {kind}")
        except asyncio.CancelledError:
            await self.store.action_result(
                firing_id, index, kind, "cancelled", {}, error="cancelled"
            )
            raise
        except Exception as exc:
            await self.store.action_result(
                firing_id, index, kind, "failed", {}, error=str(exc)[:1000]
            )
            raise
        await self.store.action_result(firing_id, index, kind, "completed", result)
        return result

    async def _llm(
        self, firing_id: str, rule: Rule, event: NormalizedEvent, action: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.config.automation_enabled:
            raise ValueError("automation kill switch is off")
        if not event.agent_run_id or not event.session_id:
            raise ValueError("automatic observers require a live mux-owned agent run")
        session = self.sessions.sessions.get(event.session_id)
        if not session or session.record.agent_run_id != event.agent_run_id:
            raise ValueError("agent run is no longer live")
        minimum_capability = str(action.get("minimum_capability") or "semantic")
        if CAPABILITY_RANK.get(event.capability, 0) < CAPABILITY_RANK[minimum_capability]:
            raise ValueError(
                f"observer requires {minimum_capability} observation; "
                f"current capability is {event.capability}"
            )
        if session.record.parser_status == "degraded" and event.source != "native_hook":
            raise ValueError("observer disabled because transcript parsing is degraded")
        if not session.transcript_path or not session.transcript_path.exists():
            raise ValueError("normalized transcript is unavailable")
        model_setting = str(action.get("model") or "cheap")
        model = {
            "cheap": self.config.openrouter_cheap_model,
            "standard": self.config.openrouter_standard_model,
        }.get(model_setting, model_setting)
        if not model:
            raise ValueError(f"OpenRouter {model_setting} model is not configured")
        global_spend = await self.store.spend()
        rule_spend = await self.store.spend(rule_id=rule.id)
        if global_spend["tokens"] >= self.config.automation_daily_token_budget:
            raise ValueError("global daily observer token budget is exhausted")
        if global_spend["cost_usd"] >= self.config.automation_daily_budget_usd:
            raise ValueError("global daily observer dollar budget is exhausted")
        if rule_spend["tokens"] >= self.config.automation_rule_daily_token_budget:
            raise ValueError("rule daily observer token budget is exhausted")
        if rule_spend["cost_usd"] >= self.config.automation_rule_daily_budget_usd:
            raise ValueError("rule daily observer dollar budget is exhausted")
        hour_ago = time.time() - 3600
        if await self.store.observer_call_count(hour_ago) >= self.config.automation_hourly_call_cap:
            raise ValueError("global hourly observer call cap is exhausted")
        if (
            await self.store.observer_call_count(hour_ago, rule_id=rule.id)
            >= self.config.automation_rule_hourly_call_cap
        ):
            raise ValueError("rule hourly observer call cap is exhausted")
        input_spec = action.get("input") or {}
        slice_kind = str(input_spec.get("slice") or "last_turn")
        if slice_kind == "summary_chain":
            summaries = await self.store.annotations(
                agent_run_id=event.agent_run_id, tag="turn-summary", limit=24
            )
            if not summaries:
                raise ValueError("summary chain is unavailable")
            transcript = self.slices.from_annotations(summaries)
        else:
            since_ts: float | None = None
            if slice_kind == "since_event":
                since_ts = float(input_spec.get("since_ts") or event.ts)
            elif slice_kind == "since_annotation":
                tag = str(input_spec.get("tag") or "turn-summary")
                previous = await self.store.recent_annotation(event.agent_run_id, tag, 0)
                since_ts = float(previous["created_at"]) if previous else None
            transcript = await self.slices.build(
                session.transcript_path,
                session.record.backend,
                slice_kind,
                max_messages=min(int(input_spec.get("messages") or 12), MAX_SLICE_MESSAGES),
                max_bytes=min(self.config.automation_max_input_tokens * 4, MAX_SLICE_BYTES),
                since_ts=since_ts,
            )
        if transcript.estimated_tokens > self.config.automation_max_input_tokens:
            raise ValueError("observer input exceeds the configured token limit")
        maximum_call_tokens = transcript.estimated_tokens + self.config.automation_max_output_tokens
        if (
            int(global_spend["tokens"]) + maximum_call_tokens
            > self.config.automation_daily_token_budget
        ):
            raise ValueError("conservative preflight estimate exceeds the global token budget")
        if (
            int(rule_spend["tokens"]) + maximum_call_tokens
            > self.config.automation_rule_daily_token_budget
        ):
            raise ValueError("conservative preflight estimate exceeds the rule token budget")
        catalog = await self.store.model_cache()
        metadata = next((item for item in catalog["models"] if item.get("id") == model), None)
        if metadata:
            estimate = transcript.estimated_tokens * float(metadata.get("prompt_price") or 0)
            estimate += self.config.automation_max_output_tokens * float(
                metadata.get("completion_price") or 0
            )
        else:
            # An unknown or uncached model used to skip the dollar preflight
            # entirely, so an empty catalog (first run, a failed refresh) silently
            # disabled the dollar bound. Price it conservatively instead.
            estimate = UNPRICED_CALL_ESTIMATE_USD
        if estimate > self.config.automation_daily_budget_usd - float(global_spend["cost_usd"]):
            raise ValueError("conservative preflight estimate exceeds the global budget")
        if estimate > self.config.automation_rule_daily_budget_usd - float(rule_spend["cost_usd"]):
            raise ValueError("conservative preflight estimate exceeds the rule budget")
        schema_name = str(action["schema"])
        prompt = str(action.get("prompt") or "Analyze the transcript and return JSON.")
        call_id = await self.store.observer_started(
            firing_id=firing_id,
            rule_id=rule.id,
            model=model,
            input_hash=transcript.input_hash,
            input_bytes=transcript.bytes,
        )
        try:
            completion = await self.provider.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": prompt[:8000]},
                    {"role": "user", "content": transcript.render()},
                ],
                schema_name=schema_name,
                schema=OBSERVER_SCHEMAS[schema_name],
                max_tokens=self.config.automation_max_output_tokens,
            )
        except asyncio.CancelledError:
            await self.store.observer_finished(call_id, status="cancelled", error="cancelled")
            raise
        except (OpenRouterError, ValueError) as exc:
            await self.store.observer_finished(call_id, status="failed", error=str(exc)[:1000])
            raise
        try:
            _validate_result(completion.value, OBSERVER_SCHEMAS[schema_name])
        except ValueError as exc:
            await self._record_observer_usage(
                call_id, rule, completion, status="failed", error=str(exc)[:1000]
            )
            raise
        await self._record_observer_usage(call_id, rule, completion, status="completed")
        return await self._observer_result(rule, event, action, completion, call_id)

    async def _record_observer_usage(
        self,
        call_id: str,
        rule: Rule,
        completion: OpenRouterResult,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        await self.store.observer_finished(
            call_id,
            status=status,
            resolved_model=completion.resolved_model,
            generation_id=completion.generation_id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
            error=error,
        )
        await self.store.add_spend(
            rule_id=rule.id,
            model=completion.resolved_model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd or 0,
            call_id=call_id,
        )
        if completion.cost_usd is None and completion.generation_id:
            task = asyncio.create_task(
                self._reconcile_cost(call_id, completion.generation_id),
                name=f"openrouter-cost-{call_id}",
            )
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _reconcile_cost(self, call_id: str, generation_id: str) -> None:
        """Land a call's real cost in the ledger, or record that it never landed.

        A dropped reconcile is not neutral: the ledger keeps the 0 that
        `add_spend` wrote, so the daily dollar budget under-counts that call
        forever and the bound silently loosens with every failure. Bounded
        retries first; a give-up is counted and logged rather than swallowed.
        """
        for attempt in range(RECONCILE_ATTEMPTS):
            try:
                cost = await self.provider.generation_cost(generation_id)
            except asyncio.CancelledError:
                self._unreconciled_calls += 1
                raise
            except OpenRouterError as exc:
                if attempt == RECONCILE_ATTEMPTS - 1:
                    self._unreconciled_calls += 1
                    self._last_reconcile_error = str(exc)[:200]
                    log.warning(
                        "observer cost reconcile failed for %s: %s; the daily dollar "
                        "budget under-counts this call",
                        call_id,
                        exc,
                    )
                    return
                await asyncio.sleep(RECONCILE_BACKOFF_SECONDS * (attempt + 1))
                continue
            if cost is not None:
                await self.store.reconcile_spend(call_id, cost)
            else:
                self._unreconciled_calls += 1
                self._last_reconcile_error = "provider reported no cost for the generation"
            return

    async def _observer_result(
        self,
        rule: Rule,
        event: NormalizedEvent,
        action: dict[str, Any],
        completion: OpenRouterResult,
        call_id: str,
    ) -> dict[str, Any]:
        mapping = action["on_result"]
        kind = str(mapping["kind"])
        if kind == "annotate":
            assert event.agent_run_id is not None
            annotation = await self.store.create_annotation(
                agent_run_id=event.agent_run_id,
                session_id=event.session_id,
                tag=str(mapping.get("tag") or "observer")[:80],
                content=_template(
                    str(mapping.get("content") or "{result.summary}"),
                    event.snapshot(),
                    completion.value,
                )[:4000],
                source_event_seq=event.seq,
                rule_id=rule.id,
                rule_revision=rule.revision,
                provenance="openrouter_observer",
                requested_model=completion.requested_model,
                resolved_model=completion.resolved_model,
                generation_id=completion.generation_id,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=completion.cost_usd,
                confidence=_float(completion.value.get("confidence")),
            )
            await self.events.emit(
                "annotation_created",
                session_id=event.session_id,
                source="automation",
                annotation_id=annotation["id"],
                tag=annotation["tag"],
                rule_id=rule.id,
                _chain_id=event.chain_id,
                _chain_depth=event.chain_depth + 1,
                _chain_rules=[*event.chain_rules, rule.id],
            )
            return {"observer_call_id": call_id, "annotation_id": annotation["id"]}
        notification = await self.store.notify(
            agent_run_id=event.agent_run_id,
            session_id=event.session_id,
            rule_id=rule.id,
            kind="observer",
            title=_template(
                str(mapping.get("title") or rule.name), event.snapshot(), completion.value
            ),
            message=_template(
                str(mapping.get("message") or "{result.summary}"),
                event.snapshot(),
                completion.value,
            ),
            severity=str(mapping.get("severity") or "info"),
        )
        await self.events.emit(
            "notification_created",
            session_id=event.session_id,
            source="automation",
            notification_id=notification["id"],
            kind=notification["kind"],
            _chain_id=event.chain_id,
            _chain_depth=event.chain_depth + 1,
            _chain_rules=[*event.chain_rules, rule.id],
        )
        return {"observer_call_id": call_id, "notification_id": notification["id"]}

    def _builtin_rules(self, event: NormalizedEvent) -> list[Rule]:
        if not event.agent_run_id:
            return []
        # Built-in rules are a pure function of (event.type, the three observer
        # flags); revision is a deterministic sha256, so the parsed Rule set is
        # byte-identical every time. Memoise it (loop-thread only, no lock) to skip
        # re-parsing+validating+hashing on every event. A flipped flag yields a new
        # key and a fresh build; the stale entry is harmless. Tracked inputs:
        # event.type + observer_titler / observer_summarizer / phase7 flags.
        cache_key = (
            event.type,
            self.config.observer_titler_enabled,
            self.config.observer_summarizer_enabled,
            self.config.phase7_observers_enabled,
        )
        cached = self._builtin_rule_cache.get(cache_key)
        if cached is not None:
            return cached
        raw: list[dict[str, Any]] = []
        if event.type == "turn_ended" and self.config.observer_titler_enabled:
            raw.append(
                {
                    "id": "builtin.session-titler",
                    "name": "Session titler",
                    "enabled": True,
                    "shadow": False,
                    "on": {"trigger": "turn_ended", "debounce_s": 1.0},
                    "when": [],
                    "do": [
                        {
                            "kind": "llm",
                            "model": "cheap",
                            "input": {"slice": "last_turn"},
                            "prompt": (
                                "Create a compact task-oriented title for a terminal tab and "
                                "sidebar. Prefer 2-3 words and never exceed 4; the tab is narrow, "
                                "so shorter wins whenever it stays accurate. Describe the "
                                "concrete user goal or work topic, and drop filler words rather "
                                "than the distinguishing one. "
                                "Never prefix with Terminal Session, Session, Claude, "
                                "Codex, User, or Conversation. Do not label simple greetings as "
                                "greetings. Return only the schema."
                            ),
                            "schema": "title_v1",
                            "on_result": {
                                "kind": "annotate",
                                "tag": "title",
                                "content": "{result.title}",
                            },
                        }
                    ],
                }
            )
        if event.type == "turn_ended" and self.config.observer_summarizer_enabled:
            raw.append(
                {
                    "id": "builtin.turn-summarizer",
                    "name": "Turn summarizer",
                    "enabled": True,
                    "shadow": False,
                    "on": {"trigger": "turn_ended", "debounce_s": 1.0},
                    "when": [],
                    "do": [
                        {
                            "kind": "llm",
                            "model": "cheap",
                            "input": {"slice": "last_turn"},
                            "prompt": (
                                "Summarize the completed turn in one factual line. "
                                "Return only the schema."
                            ),
                            "schema": "summary_v1",
                            "on_result": {
                                "kind": "annotate",
                                "tag": "turn-summary",
                                "content": "{result.summary}",
                            },
                        }
                    ],
                }
            )
        if self.config.phase7_observers_enabled and event.type in {
            "stalled",
            "approval_needed",
        }:
            label = "stalled run" if event.type == "stalled" else "approval request"
            raw.append(
                {
                    "id": f"builtin.{event.type}-triage",
                    "name": f"{label.title()} triage",
                    "enabled": True,
                    "shadow": False,
                    "on": event.type,
                    "when": [],
                    "do": [
                        {
                            "kind": "llm",
                            "model": "cheap",
                            "input": {
                                "slice": (
                                    "summary_chain" if event.type == "stalled" else "last_turn"
                                )
                            },
                            "prompt": (
                                f"Triage this {label}. Explain whether it needs user attention "
                                "without approving, rejecting, typing, or directing the agent."
                            ),
                            "schema": "attention_v1",
                            "on_result": {
                                "kind": "notify",
                                "title": label.title(),
                                "message": "{result.summary}",
                                "severity": "warning",
                            },
                        }
                    ],
                }
            )
        if self.config.phase7_observers_enabled and event.type == "context_pressure":
            raw.append(
                {
                    "id": "builtin.context-handoff",
                    "name": "Context handoff suggestion",
                    "enabled": True,
                    "shadow": False,
                    "on": "context_pressure",
                    "when": [],
                    "do": [
                        {
                            "kind": "llm",
                            "model": "standard",
                            "input": {"slice": "last_n_messages", "messages": 18},
                            "prompt": (
                                "Draft a concise handoff suggestion from the recent conversation. "
                                "Do not issue commands or inject text into the agent."
                            ),
                            "schema": "summary_v1",
                            "on_result": {
                                "kind": "annotate",
                                "tag": "handoff-suggestion",
                                "content": "{result.summary}",
                            },
                        }
                    ],
                }
            )
        rules = [
            rule for item in raw for rule in parse_rules(_rule_document(item), source="builtin")
        ]
        self._builtin_rule_cache[cache_key] = rules
        return rules

    def status(self) -> dict[str, Any]:
        adapters: dict[str, dict[str, Any]] = {}
        for session in self.sessions.sessions.values():
            backend = session.record.backend
            item = adapters.setdefault(
                backend,
                {
                    "live_sessions": 0,
                    "parser_ready": 0,
                    "parser_degraded": 0,
                    "event_sources": ["pty", "mux"],
                    "declared": ADAPTER_CAPABILITIES.get(backend, {}),
                    "hook_silence_degraded": 0,
                },
            )
            item["live_sessions"] += 1
            if session.record.parser_status == "ready":
                item["parser_ready"] += 1
                item["event_sources"] = ["native_hook", "transcript", "pty", "mux"]
            elif session.record.parser_status == "degraded":
                item["parser_degraded"] += 1
            if self._source_probes.get(session.record.id, {}).get("degraded"):
                item["hook_silence_degraded"] += 1
        return {
            "enabled": self.config.automation_enabled,
            "rules_path": str(self.path),
            "rules": [rule.snapshot() for rule in self.rules],
            "built_in_rules": [
                {
                    **item,
                    "enabled": bool(getattr(self.config, item["setting_key"])),
                    "shadow": False,
                    "source": "builtin",
                }
                for item in BUILTIN_OBSERVER_CATALOG
            ],
            "diagnostic": self.diagnostic,
            "last_loaded_at": self.last_loaded_at,
            "queue": {
                "size": self.queue.qsize(),
                "capacity": self.queue.maxsize,
                "dropped": self.queue_dropped,
                "loop_rejections": self.loop_rejections,
                "worker_failures": self.worker_failures,
                "worker_last_error": self.worker_last_error,
                # Calls whose real cost never made it into the ledger. A nonzero
                # count means the daily dollar budget is under-counting by an
                # unknown amount, which is the difference between a bound and a
                # guess.
                "unreconciled_calls": self._unreconciled_calls,
                "last_reconcile_error": self._last_reconcile_error,
                # Events dropped before automation ever saw them, per subscriber.
                "bus": self.events.drop_stats(),
            },
            "capabilities": {
                "event_schema_version": EVENT_SCHEMA_VERSION,
                "triggers": sorted(EVENT_PAYLOAD_FIELDS),
                "observer_schemas": sorted(OBSERVER_SCHEMAS),
                "max_chain_depth": MAX_CHAIN_DEPTH,
                "adapters": adapters,
            },
        }


def _rule_document(rule: dict[str, Any]) -> str:
    # The built-ins use the same validator without requiring a TOML serializer.
    canonical = json.dumps(rule)
    item = json.loads(canonical)
    on = item["on"]
    lines = ["version = 1", "", "[[rule]]", f"id = {json.dumps(item['id'])}"]
    lines.extend(
        [
            f"name = {json.dumps(item['name'])}",
            f"enabled = {str(item['enabled']).lower()}",
            f"shadow = {str(item['shadow']).lower()}",
            f"on = {_toml_value(on)}",
        ]
    )
    for action in item["do"]:
        lines.append("[[rule.do]]")
        for key, value in action.items():
            if isinstance(value, dict):
                pairs = ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items())
                lines.append(f"{key} = {{ {pairs} }}")
            else:
                lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines)


def serialize_rules(rules: list[Rule]) -> str:
    """Serialize the validated public rule model for typed UI mutations."""
    lines = ["version = 1"]
    for rule in rules:
        lines.extend(
            [
                "",
                "[[rule]]",
                f"id = {_toml_value(rule.id)}",
                f"name = {_toml_value(rule.name)}",
                f"enabled = {_toml_value(rule.enabled)}",
                f"shadow = {_toml_value(rule.shadow)}",
                f"on = {_toml_value({'trigger': rule.trigger, **rule.trigger_options})}",
                f"when = {_toml_value(list(rule.conditions))}",
            ]
        )
        for action in rule.actions:
            lines.append("")
            lines.append("[[rule.do]]")
            lines.extend(f"{key} = {_toml_value(value)}" for key, value in action.items())
    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items()) + " }"
        )
    raise RuleValidationError(f"unsupported TOML value: {type(value).__name__}")


def _field(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _template(template: str, event: dict[str, Any], result: dict[str, Any] | None) -> str:
    values = {"event": event, "result": result or {}}

    def replace(match: re.Match[str]) -> str:
        value = _field(values, match.group(1))
        return "" if value is None else str(value)

    return re.sub(r"\{([a-zA-Z0-9_.-]+)\}", replace, template)


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _validate_result(value: dict[str, Any], schema: dict[str, Any]) -> None:
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False and set(value) - set(properties):
        raise ValueError("observer response contained extra fields")
    for required in schema.get("required") or []:
        if required not in value:
            raise ValueError(f"observer response omitted {required}")
    for key, item in value.items():
        kind = properties.get(key, {}).get("type")
        valid_type = (
            kind == "string"
            and isinstance(item, str)
            or kind == "number"
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
            or kind == "boolean"
            and isinstance(item, bool)
            or kind not in {"string", "number", "boolean"}
        )
        if not valid_type:
            raise ValueError(f"observer response field {key} has the wrong type")
        spec = properties.get(key, {})
        if isinstance(item, str) and len(item) > int(spec.get("maxLength") or len(item)):
            raise ValueError(f"observer response field {key} is too long")
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if "minimum" in spec and item < spec["minimum"]:
                raise ValueError(f"observer response field {key} is below its minimum")
            if "maximum" in spec and item > spec["maximum"]:
                raise ValueError(f"observer response field {key} is above its maximum")


def validate_observer_result(value: dict[str, Any], schema_name: str) -> None:
    if schema_name not in OBSERVER_SCHEMAS:
        raise ValueError("unknown observer schema")
    _validate_result(value, OBSERVER_SCHEMAS[schema_name])


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
