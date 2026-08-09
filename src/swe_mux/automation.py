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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

from .automation_store import AutomationStore
from .background_tasks import background
from .config import Config
from .event_bus import EventBus
from .harness import HARNESSES
from .models import MuxEvent, SessionRecord
from .openrouter import OpenRouterClient, OpenRouterError, OpenRouterResult
from .session import SessionManager
from .text_safety import utf8_safe_value
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
        "reasoning",
    },
}
SLICE_KINDS = {
    "last_turn",
    "last_n_messages",
    "since_event",
    "since_annotation",
    "summary_chain",
    # The user's own latest request, taken from the hook ingress rather than the
    # transcript. The only slice that needs neither a parsed transcript nor
    # semantic observation, which is what lets a pane be titled immediately and
    # lets titling survive the degraded-observation states that used to fail it.
    "prompt_text",
}
TRANSCRIPT_FREE_SLICES = {"prompt_text", "summary_chain"}
# Prompt titles may move only through a bounded provisional phase. The common case
# settles on the opening request and still takes one call; setup-only requests can
# revise against at most the first three user prompts before freezing.
# `FALLBACK_TITLE_RULE_ID` exists only for adopted runs whose request was unavailable.
# The ids keep their original strings so annotations, user rules, and settings keys
# continue to resolve.
#
# The fallback reads the completed turn, which is a much weaker signal for a name:
# it describes what just happened rather than what the session is for, and it
# produced titles like "OK" and "Reply FROZENCODEX" in practice. That is why it is
# gated on the prompt being genuinely unavailable rather than merely not-yet-used.
PROMPT_TITLE_RULE_ID = "builtin.session-titler-initial"
FALLBACK_TITLE_RULE_ID = "builtin.session-titler"
TITLE_RULE_IDS = {PROMPT_TITLE_RULE_ID, FALLBACK_TITLE_RULE_ID}
TITLE_STATE_CHECKPOINT_PREFIX = "title-state:"
TITLE_MAX_AUTOMATIC_CALLS = 3
TITLE_MAX_AUTOMATIC_PROMPTS = 3
# A title lost to a provider rate limit used to wait for the next turn boundary,
# which on an idle pane never comes: sessions were observed sitting nameless for
# 20+ minutes with the user waiting on nothing. Retry in the background instead.
#
# The curve is sized against the failure that actually happens. The first three
# steps ride out a burst of concurrent panes. The last three exist because an
# upstream provider outage is measured in hours, not minutes: on 2026-07-31 a
# 30s/2m/5m ladder gave up after eight minutes and every session opened that day
# stayed nameless, because a run that has stopped retrying and is sitting idle has
# nothing left to trigger it. Total horizon is a little over two hours.
TITLE_RETRY_DELAYS_SECONDS = (30.0, 120.0, 300.0, 900.0, 2700.0, 5400.0)
# Checkpoint namespace for the pinned first prompt of a run. In the store rather
# than only on the Session because the daemon restarts (every reload, every
# redeploy) while its sessions keep running, and the in-memory pin dies with it.
RUN_PROMPT_CHECKPOINT_PREFIX = "run-prompt:"
# Checkpoint namespace for a title attempt waiting to be retried. Same reason as
# the prompt pin, and more acutely: the retry horizon is now longer than the gap
# between two redeploys, so a purely in-memory timer would rarely survive to fire.
TITLE_RETRY_CHECKPOINT_PREFIX = "title-retry:"
# How often due retries are swept. The shortest delay is 30s, so a coarse tick adds
# no meaningful latency and keeps the interval loop's per-second work at one indexed
# lookup rather than a table scan.
TITLE_RETRY_SWEEP_SECONDS = 5.0
# Firings per sweep. A provider returning after an outage makes every waiting run due
# at the same instant, and each firing is a network call made inline on the loop that
# also fires timer rules. The overflow is due again on the next tick.
TITLE_RETRY_SWEEP_LIMIT = 4
# Ceiling on a provider-supplied `Retry-After`. Honouring an hours-long hint
# verbatim would park the last attempt past the point where a name is still useful.
MAX_TITLE_RETRY_DELAY_SECONDS = 900.0
CAPABILITY_RANK = {"telemetry": 1, "inferred": 1, "semantic": 2, "derived": 2, "trusted": 3}
ADAPTER_CAPABILITIES: dict[str, dict[str, Any]] = {
    **{
        name: harness.automation_capabilities() for name, harness in HARNESSES.items()
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
        "id": "builtin.session-titler-initial",
        "name": "Session titler",
        "setting_key": "observer_titler_enabled",
        "setting_label": "Session titler",
        "trigger": "turn_started",
        "input": "The request the run opened with",
        "model": "Cheap model",
        "result": "Run note used as the generated session title",
        "description": "Names a pane once, from the request that started the run.",
    },
    {
        "id": "builtin.session-titler",
        "name": "Session titler (no prompt)",
        "setting_key": "observer_titler_enabled",
        "setting_label": "Session titler",
        "trigger": "turn_ended",
        "input": "Last completed turn",
        "model": "Cheap model",
        "result": "Run note used as the generated session title",
        "description": "Fallback for runs with no captured request, such as Codex.",
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
    "transcript_message": {"role"},
    "title_regenerate_requested": {"force_title"},
    "tool_use": {"tool", "detail", "target"},
    "tool_result": {"tool", "success", "exit_code", "detail"},
    "approval_needed": {"kind", "detail"},
    "state_changed": {"previous", "state", "detail"},
    "runtime_cwd_changed": {"dropped"},
    "git_changed": {"branch", "dirty", "ahead", "behind", "worktree", "added", "removed"},
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
    "queue_delivery": {
        "message_id",
        "outcome",
        "delivery_state",
        "confirmed",
        "initiator",
        "bytes",
    },
    # Phase 5: auto-delivery opt-in changes and bounded agent-to-agent
    # messages. Ids, kinds, and counts only — never a message body.
    "queue_auto_policy": {"enabled", "reason"},
    "queue_message_received": {"message_id", "sender_kind", "from_session", "chain_depth"},
    "spawn_request_drafted": {"request_id", "project_id", "from_session"},
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
    "title_v2": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "confidence", "stability"],
        "properties": {
            "title": {"type": "string", "maxLength": 80},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "stability": {"type": "string", "enum": ["provisional", "settled"]},
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


_EVENT_FIELDS = frozenset(field.name for field in fields(NormalizedEvent))


def _serializable_event(event: NormalizedEvent) -> dict[str, Any]:
    """A JSON round-trippable form of an event, for work that outlives the process."""
    snapshot = event.snapshot()
    snapshot["chain_rules"] = list(event.chain_rules)
    return snapshot


def _event_from_snapshot(snapshot: dict[str, Any]) -> NormalizedEvent:
    """Rebuild an event persisted by `_serializable_event`.

    Unknown keys are dropped rather than raising, so a snapshot written before a
    field was added still replays; missing required keys raise `TypeError`, which
    the caller treats as an unreplayable row.
    """
    values = {key: value for key, value in snapshot.items() if key in _EVENT_FIELDS}
    values["chain_rules"] = tuple(values.get("chain_rules") or ())
    return NormalizedEvent(**values)


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
    if "reasoning" in action and not isinstance(action["reasoning"], bool):
        raise RuleValidationError(f"rule {rule_id} llm reasoning must be a boolean")


def _encodable_messages(
    messages: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bytes]:
    """Slice messages made UTF-8 representable, with the bytes they serialize to.

    Every slice is hashed and measured by encoding it, and every observer input is
    rendered from the same messages, so this is the one place both can be made
    total. A lone surrogate reaching here used to raise `UnicodeEncodeError` — a
    `ValueError`, so it was caught as an observer fault and the run lost its title
    permanently, four layers from the hook that decoded the byte wrong.
    """
    safe = cast(list[dict[str, Any]], utf8_safe_value(list(messages)))
    return safe, json.dumps(safe, separators=(",", ":"), ensure_ascii=False).encode()


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
        selected, encoded = _encodable_messages(selected)
        while selected and len(encoded) > max_bytes:
            selected, encoded = _encodable_messages(selected[1:])
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
    def from_prompt(text: str, kind: str = "prompt_text") -> TranscriptSlice:
        """A one-message slice holding the user's request, read from no file."""
        raw = [{"role": "user", "ts": time.time(), "content": [{"type": "text", "text": text}]}]
        messages, encoded = _encodable_messages(raw)
        return TranscriptSlice(
            kind,
            tuple(messages),
            len(encoded),
            max(1, len(encoded) // 4),
            False,
            hashlib.sha256(encoded).hexdigest(),
        )

    @staticmethod
    def from_annotations(
        items: list[dict[str, Any]], kind: str = "summary_chain"
    ) -> TranscriptSlice:
        raw = [
            {
                "role": "assistant",
                "ts": item["created_at"],
                "content": [{"type": "text", "text": item["content"]}],
            }
            for item in reversed(items[-24:])
        ]
        messages, encoded = _encodable_messages(raw)
        return TranscriptSlice(
            kind,
            tuple(messages),
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
        # Instance-level so a test can drive the retry curve without sleeping it.
        self._title_retry_delays: tuple[float, ...] = TITLE_RETRY_DELAYS_SECONDS
        self._retry_seq = 0
        self._title_sweep_next = 0.0
        # Refreshed by the sweep so the sync status surface can report it. A nonzero
        # `exhausted` is the visible form of "this pane will not get a name".
        self._title_retry_counts = {"pending": 0, "exhausted": 0}
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
                # Rides the same supervised loop rather than a timer of its own: a
                # retry that only exists in the store needs something durable to
                # notice it, and this loop is already restarted on failure.
                now = time.time()
                if now >= self._title_sweep_next:
                    self._title_sweep_next = now + TITLE_RETRY_SWEEP_SECONDS
                    await self._sweep_title_retries(now=now)

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
                    await self._clear_title_retry(rule, event)
                except asyncio.CancelledError:
                    await self.store.finish_firing(firing_id, "cancelled", "cancelled")
                    raise
                except Exception as exc:
                    await self.store.finish_firing(firing_id, "failed", str(exc)[:1000])
                    report["error"] = str(exc)
                    if await self._schedule_title_retry(rule, event, exc):
                        report["retry_scheduled"] = True
            finally:
                if unique_key:
                    self._unique_inflight.discard(unique_key)
        return reports

    @staticmethod
    def _title_retry_key(rule_id: str, agent_run_id: str) -> str:
        return f"{TITLE_RETRY_CHECKPOINT_PREFIX}{rule_id}:{agent_run_id}"

    async def _schedule_title_retry(
        self, rule: Rule, event: NormalizedEvent, error: BaseException
    ) -> bool:
        """Re-attempt a title the provider refused, without waiting for a turn boundary.

        A session that goes idle right after a rate-limited title has no next turn to
        piggyback on, so the pane keeps the backend's placeholder name until the user
        happens to type again. Retrying in the background is what closes that.

        The attempt is written to the store rather than held in an `asyncio.sleep`,
        because the horizon it has to cover (hours, for an upstream outage) is longer
        than this daemon's uptime between reloads. `_sweep_title_retries` is what
        fires it, here or in the successor process.

        Only *retryable* provider failures qualify. A refused key fails identically
        forever. Everything else an observer raises, including budget exhaustion,
        degraded observation, or a missing prompt, is a decision, not a fault.
        Retrying those only spends calls.
        """
        if rule.id not in TITLE_RULE_IDS or not event.agent_run_id:
            return False
        if not isinstance(error, OpenRouterError) or not error.retryable:
            await self._clear_title_retry(rule, event)
            return False
        attempt = int(event.payload.get("title_retry") or 0)
        key = self._title_retry_key(rule.id, event.agent_run_id)
        if attempt >= len(self._title_retry_delays):
            # Exhausted, and recorded rather than forgotten. The record is what lets
            # the no-prompt fallback stop standing down, and what tells a human
            # reading the pane's state why it never got a name.
            await self.store.set_checkpoint(
                key,
                {
                    "rule_id": rule.id,
                    "agent_run_id": event.agent_run_id,
                    "session_id": event.session_id,
                    "attempt": attempt,
                    "exhausted": True,
                    "last_error": str(error)[:500],
                    "updated_at": time.time(),
                },
            )
            return False
        delay = self._title_retry_delays[attempt]
        # The provider's own `Retry-After` beats a fixed curve when it is the longer
        # of the two: it is the only party that knows when the limit actually lifts.
        if error.retry_after is not None:
            delay = max(delay, min(float(error.retry_after), MAX_TITLE_RETRY_DELAY_SECONDS))
        payload = {**event.payload, "title_retry": attempt + 1}
        if attempt + 1 == len(self._title_retry_delays) and self._escalation_model():
            # Last resort: a whole model's provider pool can be rate-limited at once
            # (that is precisely what happened on 2026-07-31 — every provider serving
            # the cheap model refused while the standard model answered on the first
            # try). Switching model switches pool. Only on the final attempt, so the
            # normal path stays on the model the user chose and paid for.
            payload["observer_model"] = "standard"
        await self.store.set_checkpoint(
            key,
            {
                "rule_id": rule.id,
                "agent_run_id": event.agent_run_id,
                "session_id": event.session_id,
                "attempt": attempt + 1,
                "due_at": time.time() + delay,
                "last_error": str(error)[:500],
                "updated_at": time.time(),
                "event": _serializable_event(replace(event, payload=payload)),
            },
        )
        log.warning(
            "title retry scheduled rule=%s run=%s session=%s attempt=%s delay_s=%s error=%s",
            rule.id,
            event.agent_run_id,
            event.session_id,
            attempt + 1,
            delay,
            str(error)[:200],
        )
        return True

    def _escalation_model(self) -> str:
        """The standard model, when it is a genuinely different one to fall back to."""
        standard = str(self.config.openrouter_standard_model or "")
        return "" if standard == str(self.config.openrouter_cheap_model or "") else standard

    async def _prompt_titler_gave_up(self, event: NormalizedEvent) -> bool:
        """Whether the prompt titler has spent every attempt on this run."""
        if not event.agent_run_id:
            return False
        key = self._title_retry_key(PROMPT_TITLE_RULE_ID, event.agent_run_id)
        return bool((await self.store.checkpoint(key) or {}).get("exhausted"))

    async def _clear_title_retry(self, rule: Rule, event: NormalizedEvent) -> None:
        """Drop a run's pending retry once the question it was asking is answered."""
        if rule.id not in TITLE_RULE_IDS or not event.agent_run_id:
            return
        await self.store.clear_checkpoint(self._title_retry_key(rule.id, event.agent_run_id))

    async def _sweep_title_retries(self, *, now: float | None = None) -> int:
        """Fire every title retry whose delay has elapsed, wherever it was scheduled.

        Runs from the interval loop, so a retry written by a daemon that has since
        been reloaded or redeployed is picked up by its successor — which is the
        common case once the curve stretches past a few minutes.

        Bounded per pass. Each firing is a network call made inline on the loop that
        also fires timer rules, and a provider coming back after an outage releases
        every waiting run at once. The remainder is simply due again in five seconds.
        """
        moment = time.time() if now is None else now
        fired = 0
        pending = exhausted = 0
        due_rows: list[tuple[str, dict[str, Any]]] = []
        for key, value in await self.store.checkpoints_with_prefix(TITLE_RETRY_CHECKPOINT_PREFIX):
            if value.get("exhausted"):
                exhausted += 1
                continue
            pending += 1
            due = value.get("due_at")
            if isinstance(due, int | float) and moment >= float(due):
                due_rows.append((key, value))
        # Oldest due first, so a run that has been waiting longest is not starved by
        # the arbitrary key order a later one happens to sort under.
        due_rows.sort(key=lambda row: float(row[1].get("due_at") or 0))
        for key, value in due_rows[:TITLE_RETRY_SWEEP_LIMIT]:
            snapshot = value.get("event")
            event = None
            if isinstance(snapshot, dict):
                try:
                    event = _event_from_snapshot(snapshot)
                except (TypeError, ValueError):
                    event = None
            # Both titlers are built-ins, and a built-in only exists as a Rule for the
            # event that produced it — which is exactly the event being replayed.
            candidates = [] if event is None else [*self.rules, *self._builtin_rules(event)]
            rule = next((item for item in candidates if item.id == value.get("rule_id")), None)
            if event is None or rule is None or not rule.enabled:
                # Unreplayable, or the rule was disabled or edited out from under a
                # pending retry. Nothing will ever fire it: litter rather than work.
                await self.store.clear_checkpoint(key)
                pending -= 1
                continue
            self._retry_seq -= 1
            # Firings are unique on (event_seq, rule_id, rule_revision), so a retry of
            # the same event needs a sequence of its own. Counting down from zero keeps
            # retry firings distinguishable and can never collide with the bus's own.
            event = replace(event, seq=self._retry_seq, ts=moment)
            fired += 1
            # Straight back through the guards: by now the run may have ended, been
            # cleared, or been titled by the other stage, and each of those is a
            # reason to stop that the guards already know how to state.
            reports = await self.evaluate(event, rules=[rule], debounced=True)
            if any(report.get("retry_scheduled") for report in reports):
                continue
            current = await self.store.checkpoint(key)
            if current is None:
                # The title landed and took its own row with it.
                pending -= 1
                continue
            if current.get("exhausted"):
                # Ran out of attempts on this pass. The marker is the record of that
                # and must outlive the sweep that produced it.
                pending -= 1
                exhausted += 1
                continue
            if current.get("attempt") == value.get("attempt"):
                # The row came back untouched: the firing was guarded off, or failed
                # for a reason that does not retry. Either way nothing rescheduled it,
                # and leaving the row behind would re-fire it every sweep forever.
                await self.store.clear_checkpoint(key)
                pending -= 1
        self._title_retry_counts = {"pending": pending, "exhausted": exhausted}
        return fired

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
        if rule.id in TITLE_RULE_IDS and event.agent_run_id:
            session = self.sessions.sessions.get(event.session_id or "")
            if session and session.record.auto_named is False:
                return False
            title = await self.store.recent_annotation(event.agent_run_id, "title", 0)
            if rule.id == FALLBACK_TITLE_RULE_ID:
                if title is not None:
                    return False
                if (
                    await self._run_prompt(event, pin=not dry_run) is not None
                    and not await self._prompt_titler_gave_up(event)
                ):
                    # A captured request is stronger than the completed-turn fallback.
                    return False
            else:
                # Record every observed prompt before deciding whether the generated
                # title is frozen. This keeps a later explicit regenerate action useful
                # without adding transcript polling or a separate classifier call.
                await self._run_prompt(event, pin=not dry_run)
                retry_state = await self.store.checkpoint(
                    self._title_retry_key(rule.id, event.agent_run_id)
                )
                if retry_state and event.seq >= 0 and not event.payload.get("force_title"):
                    # A turn-end repair event must not start a second ladder while
                    # the opening attempt is pending or after it has exhausted.
                    # Pending retries keep their pinned prompt; exhausted retries
                    # hand the run to the weaker completed-turn fallback.
                    return False
                if title is not None and not event.payload.get("force_title"):
                    state = await self.store.checkpoint(
                        f"{TITLE_STATE_CHECKPOINT_PREFIX}{event.agent_run_id}"
                    )
                    if not state and title.get("evidence_json"):
                        try:
                            evidence = json.loads(str(title["evidence_json"]))
                            recovered = next(
                                (
                                    item
                                    for item in evidence
                                    if isinstance(item, dict)
                                    and item.get("kind") == "title_lifecycle"
                                ),
                                None,
                            )
                        except (json.JSONDecodeError, TypeError):
                            recovered = None
                        if recovered:
                            state = {
                                "stability": recovered.get("stability"),
                                "titled_prompt_count": recovered.get("prompt_count"),
                                "automatic_calls": recovered.get("automatic_calls"),
                            }
                            if not dry_run:
                                await self.store.set_checkpoint(
                                    f"{TITLE_STATE_CHECKPOINT_PREFIX}{event.agent_run_id}",
                                    {**state, "recovered_at": time.time()},
                                )
                    # Missing state means a legacy title. Preserve the old invariant
                    # rather than unexpectedly renaming existing sessions after upgrade.
                    if not state or state.get("stability") != "provisional":
                        return False
                    prompt_state = await self.store.checkpoint(
                        f"{RUN_PROMPT_CHECKPOINT_PREFIX}{event.agent_run_id}"
                    )
                    prompt_count = int((prompt_state or {}).get("prompt_count") or 0)
                    titled_count = int(state.get("titled_prompt_count") or 0)
                    automatic_calls = int(state.get("automatic_calls") or 0)
                    if (
                        prompt_count <= titled_count
                        or prompt_count > TITLE_MAX_AUTOMATIC_PROMPTS
                        or automatic_calls >= TITLE_MAX_AUTOMATIC_CALLS
                    ):
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
        if rule.id in TITLE_RULE_IDS and event.agent_run_id:
            return rule.id, event.agent_run_id
        return None

    async def _run_prompt(self, event: NormalizedEvent, *, pin: bool = True) -> str | None:
        """Bounded user-request context for the title lifecycle.

        The first three distinct prompts support automatic provisional revision;
        ``latest`` keeps an explicit regenerate useful later. A failed provider call
        pins its active input separately, so a scheduled retry asks the same question
        even if the user has moved on. ``pin=False`` leaves dry runs read-only.
        """
        if not event.agent_run_id:
            return None
        key = f"{RUN_PROMPT_CHECKPOINT_PREFIX}{event.agent_run_id}"
        pinned = dict(await self.store.checkpoint(key) or {})
        prompts = [
            str(item)
            for item in pinned.get("prompts", [])
            if isinstance(item, str) and item
        ][:TITLE_MAX_AUTOMATIC_PROMPTS]
        first = str(pinned.get("text") or "")
        if first and not prompts:
            prompts = [first]
        latest = str(pinned.get("latest") or (prompts[-1] if prompts else first))
        prompt_count = int(pinned.get("prompt_count") or len(prompts))
        session = self.sessions.sessions.get(event.session_id or "")
        if session and session.record.agent_run_id == event.agent_run_id:
            live = getattr(session, "last_user_prompt", None)
            if not isinstance(live, str) or not live:
                live = getattr(session, "first_user_prompt", None)
            if (
                isinstance(live, str)
                and live
                and not event.payload.get("title_retry")
                and live != latest
            ):
                latest = live
                prompt_count += 1
                if len(prompts) < TITLE_MAX_AUTOMATIC_PROMPTS:
                    prompts.append(live)
                if not first:
                    first = live
                if pin:
                    await self.store.set_checkpoint(
                        key,
                        {
                            "text": first,
                            "prompts": prompts,
                            "latest": latest,
                            "prompt_count": prompt_count,
                            "pinned_at": pinned.get("pinned_at") or time.time(),
                            "updated_at": time.time(),
                        },
                    )
        elif not first:
            # A rolled-over session's prompt belongs to the successor run, not this
            # one, so a mismatched record is no prompt at all.
            return None

        if not first:
            return None
        if not pinned and pin:
            await self.store.set_checkpoint(
                key,
                {
                    "text": first,
                    "prompts": prompts or [first],
                    "latest": latest or first,
                    "prompt_count": prompt_count or 1,
                    "pinned_at": time.time(),
                    "updated_at": time.time(),
                },
            )

        title_state = await self.store.checkpoint(
            f"{TITLE_STATE_CHECKPOINT_PREFIX}{event.agent_run_id}"
        )
        active = str((title_state or {}).get("active_prompt_text") or "")
        automatic_calls = int((title_state or {}).get("automatic_calls") or 0)
        if event.payload.get("title_retry") and active:
            return active
        # Until the first title lands, preserve the opening input across fresh turn
        # events too. Once a provisional title exists, a new prompt intentionally
        # replaces the active context with the accumulated request sequence.
        if active and automatic_calls == 0 and not event.payload.get("force_title"):
            return active
        if event.payload.get("force_title"):
            return latest or first
        selected = prompts or [first]
        if len(selected) == 1:
            return selected[0]
        return "\n\n".join(
            f"Request {index}: {text}" for index, text in enumerate(selected, start=1)
        )

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
        if session.record.observation_stale_since is not None:
            # The transcript parses fine; it is just no longer this session's
            # conversation (an unfollowable /clear or /new). An observer reading it
            # would title and summarize a conversation the user has already left.
            raise ValueError("observer disabled because the followed transcript is stale")
        needs_transcript = (
            str((action.get("input") or {}).get("slice") or "last_turn")
            not in TRANSCRIPT_FREE_SLICES
        )
        if needs_transcript and (
            not session.transcript_path or not session.transcript_path.exists()
        ):
            raise ValueError("normalized transcript is unavailable")
        model_setting = str(action.get("model") or "cheap")
        # A retry that has run out of road may ask for the other tier; see
        # `_schedule_title_retry`. Only ever an escalation the engine set on its own
        # retry event, never something an incoming event can carry in from outside.
        escalated = str(event.payload.get("observer_model") or "")
        if escalated in {"cheap", "standard"} and event.seq < 0:
            model_setting = escalated
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
        if slice_kind == "prompt_text":
            prompt_text = await self._run_prompt(event)
            if not prompt_text:
                raise ValueError("no user prompt has been observed for this run")
            if rule.id == PROMPT_TITLE_RULE_ID:
                state_key = f"{TITLE_STATE_CHECKPOINT_PREFIX}{event.agent_run_id}"
                title_state = dict(await self.store.checkpoint(state_key) or {})
                prompt_state = await self.store.checkpoint(
                    f"{RUN_PROMPT_CHECKPOINT_PREFIX}{event.agent_run_id}"
                )
                await self.store.set_checkpoint(
                    state_key,
                    {
                        **title_state,
                        "active_prompt_text": prompt_text,
                        "active_prompt_count": int(
                            (prompt_state or {}).get("prompt_count") or 1
                        ),
                        "active_force": bool(event.payload.get("force_title")),
                        "updated_at": time.time(),
                    },
                )
            transcript = self.slices.from_prompt(prompt_text)
        elif slice_kind == "summary_chain":
            summaries = await self.store.annotations(
                agent_run_id=event.agent_run_id, tag="turn-summary", limit=24
            )
            if not summaries:
                raise ValueError("summary chain is unavailable")
            transcript = self.slices.from_annotations(summaries)
        else:
            if session.transcript_path is None:
                # Unreachable: every slice kind that lands here is transcript-backed,
                # so the availability check above already ran. Stated for the reader
                # and the type checker rather than left to inference.
                raise ValueError("normalized transcript is unavailable")
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
                reasoning_enabled=cast(bool | None, action.get("reasoning")),
            )
        except asyncio.CancelledError:
            await self.store.observer_finished(call_id, status="cancelled", error="cancelled")
            raise
        except OpenRouterError as exc:
            await self.store.observer_finished(
                call_id,
                status="failed",
                resolved_model=exc.resolved_model,
                generation_id=exc.generation_id,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
                cost_usd=exc.cost_usd,
                latency_ms=exc.latency_ms,
                provider_name=exc.provider_name,
                finish_reason=exc.finish_reason,
                response_content_type=exc.response_content_type,
                response_content_length=exc.response_content_length,
                http_status=exc.status,
                retryable=exc.retryable,
                error=str(exc)[:1000],
            )
            if (
                exc.generation_id
                or exc.input_tokens
                or exc.output_tokens
                or exc.cost_usd is not None
            ):
                await self.store.add_spend(
                    rule_id=rule.id,
                    model=exc.resolved_model or model,
                    input_tokens=exc.input_tokens,
                    output_tokens=exc.output_tokens,
                    cost_usd=exc.cost_usd or 0,
                    call_id=call_id,
                )
                if exc.cost_usd is None and exc.generation_id:
                    self._schedule_cost_reconcile(call_id, exc.generation_id)
            raise
        except ValueError as exc:
            await self.store.observer_finished(call_id, status="failed", error=str(exc)[:1000])
            raise
        try:
            _validate_result(completion.value, OBSERVER_SCHEMAS[schema_name])
        except ValueError as exc:
            await self._record_observer_usage(
                call_id,
                rule,
                completion,
                status="failed",
                error=str(exc)[:1000],
                retryable=True,
            )
            raise OpenRouterError(
                f"OpenRouter structured response failed schema validation: {exc}",
                status=200,
                retryable=True,
                generation_id=completion.generation_id,
                resolved_model=completion.resolved_model,
                provider_name=completion.provider_name,
                finish_reason=completion.finish_reason,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=completion.cost_usd,
                latency_ms=completion.latency_ms,
                response_content_type=completion.response_content_type,
                response_content_length=completion.response_content_length,
            ) from exc
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
        retryable: bool | None = None,
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
            provider_name=completion.provider_name,
            finish_reason=completion.finish_reason,
            response_content_type=completion.response_content_type,
            response_content_length=completion.response_content_length,
            http_status=200,
            retryable=retryable,
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
            self._schedule_cost_reconcile(call_id, completion.generation_id)

    def _schedule_cost_reconcile(self, call_id: str, generation_id: str) -> None:
        task = asyncio.create_task(
            self._reconcile_cost(call_id, generation_id),
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
            title_lifecycle: dict[str, Any] | None = None
            if rule.id == PROMPT_TITLE_RULE_ID:
                state_key = f"{TITLE_STATE_CHECKPOINT_PREFIX}{event.agent_run_id}"
                title_state = dict(await self.store.checkpoint(state_key) or {})
                forced = bool(title_state.get("active_force"))
                stability = (
                    "settled" if forced else str(completion.value.get("stability") or "settled")
                )
                automatic_calls = int(title_state.get("automatic_calls") or 0)
                if not forced:
                    automatic_calls += 1
                title_lifecycle = {
                    "kind": "title_lifecycle",
                    "stability": stability,
                    "prompt_count": int(title_state.get("active_prompt_count") or 1),
                    "automatic_calls": automatic_calls,
                    "explicit_regenerate": forced,
                }
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
                evidence=[title_lifecycle] if title_lifecycle else None,
            )
            if title_lifecycle is not None:
                await self.store.set_checkpoint(
                    f"{TITLE_STATE_CHECKPOINT_PREFIX}{event.agent_run_id}",
                    {
                        "stability": title_lifecycle["stability"],
                        "titled_prompt_count": title_lifecycle["prompt_count"],
                        "automatic_calls": title_lifecycle["automatic_calls"],
                        "last_annotation_id": annotation["id"],
                        "updated_at": time.time(),
                    },
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
        if event.type in {
            "turn_started",
            "turn_ended",
            "transcript_message",
            "title_regenerate_requested",
        } and self.config.observer_titler_enabled:
            raw.append(
                {
                    "id": PROMPT_TITLE_RULE_ID,
                    "name": "Session titler",
                    "enabled": True,
                    "shadow": False,
                    # Debounced past the hook/transcript race: whichever source opens
                    # the turn, the prompt has to have been recorded before this runs.
                    "on": {"trigger": event.type, "debounce_s": 2.0},
                    "when": [],
                    "do": [
                        {
                            "kind": "llm",
                            "model": "cheap",
                            "reasoning": False,
                            # Reads no transcript, so it works before one exists and
                            # keeps working when observation has degraded to inferred.
                            "input": {"slice": "prompt_text"},
                            "minimum_capability": "telemetry",
                            "prompt": (
                                "Create a compact task-oriented title for a terminal tab and "
                                "sidebar from the ordered user requests. Later requests clarify "
                                "earlier ones; do not title a setup step when a concrete task is "
                                "present. Return stability=provisional only when the requests are "
                                "still setup/orientation (for example learn, review, or read docs) "
                                "and do not yet name concrete work. Otherwise return "
                                "stability=settled. "
                                "Prefer 2-3 words and "
                                "never exceed 4; the tab is narrow, so shorter wins whenever it "
                                "stays accurate. Describe the concrete user goal or work topic, "
                                "and drop filler words rather than the distinguishing one. "
                                "Never prefix with Terminal Session, Session, Claude, "
                                "Codex, User, or Conversation. Do not label simple greetings as "
                                "greetings. Return only the schema."
                            ),
                            "schema": "title_v2",
                            "on_result": {
                                "kind": "annotate",
                                "tag": "title",
                                "content": "{result.title}",
                            },
                        }
                    ],
                }
            )
        if event.type == "turn_ended" and self.config.observer_titler_enabled:
            raw.append(
                {
                    "id": FALLBACK_TITLE_RULE_ID,
                    "name": "Session titler (no prompt)",
                    "enabled": True,
                    "shadow": False,
                    "on": {"trigger": "turn_ended", "debounce_s": 1.0},
                    "when": [],
                    "do": [
                        {
                            "kind": "llm",
                            "model": "cheap",
                            "reasoning": False,
                            "input": {"slice": "last_turn"},
                            "prompt": (
                                "Create a compact task-oriented title for a terminal tab and "
                                "sidebar. Name what the user is trying to accomplish, not what "
                                "the assistant just said or did — this turn is a step inside a "
                                "longer session and the title has to survive the next ten. "
                                "Prefer 2-3 words and never exceed 4; the tab is narrow, "
                                "so shorter wins whenever it stays accurate. Describe the "
                                "concrete user goal or work topic, and drop filler words rather "
                                "than the distinguishing one. "
                                "Never prefix with Terminal Session, Session, Claude, "
                                "Codex, User, or Conversation. Do not label simple greetings as "
                                "greetings, and never answer or acknowledge the conversation. "
                                "Return only the schema."
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
                # Titles waiting on a provider that refused, and titles that ran out
                # of attempts. The second number is the one worth an eyebrow: those
                # panes keep their placeholder name for the rest of the run.
                "title_retries": dict(self._title_retry_counts),
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
