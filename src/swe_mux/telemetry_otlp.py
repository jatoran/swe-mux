"""Privacy-bounded normalization of provider OTLP/JSON log batches.

The attribute sets below were measured against real exports rather than read from
documentation: Claude Code 2.1.259 and Codex CLI 0.153.0, captured through the same
loopback ingress the daemon runs (`tests/fixtures/telemetry/otlp-*.json` are those
captures with identities and content removed). A provider that renames an attribute
degrades to "missing" on that field rather than to a guess, and every event name the
reducer does not recognise is reported back as a parser signature so the drift is
visible instead of silent.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .harness import HARNESSES, NativeTelemetry
from .models import MuxEvent

PARSER_VERSION = "otlp-json-v2"

#: Attributes that identify a person, an account, or a machine. Never copied
#: into a reduced event under any key.
IDENTITY_ATTRIBUTES = frozenset(
    {
        "user.email",
        "user.id",
        "user.account_id",
        "user.account_uuid",
        "organization.id",
        "session.id",
        "host.name",
    }
)
#: Attributes that carry content. Measured and hashed, never copied.
CONTENT_ATTRIBUTES = frozenset(
    {
        "prompt",
        "response",
        "tool_input",
        "tool_parameters",
        "arguments",
        "output",
        "tool_result",
        "error",
    }
)
_DENIED_DECISIONS = frozenset({"reject", "rejected", "denied", "deny", "abort", "aborted"})
_KNOWN_IGNORED_EVENTS = frozenset(
    {
        # Content-only, or startup inventory with no lifecycle meaning.
        "user_prompt",
        "assistant_response",
        "plugin_loaded",
        "hook_registered",
        "mcp_server_connection",
        "startup_phase",
        "conversation_starts",
        "turn_ttft",
        "websocket_connect",
        # Claude's hook-execution timing. Seen on the first live session after
        # deployment rather than in the headless capture, which configures no hooks;
        # the hook's own PostToolUse delivery already reaches the ledger, so these
        # add nothing it does not have.
        "hook_execution_start",
        "hook_execution_complete",
    }
)


def native_telemetry(backend: str) -> NativeTelemetry | None:
    """The registry's measured OTLP contract for this harness, or None."""

    harness = HARNESSES.get(backend)
    return harness.native_telemetry if harness is not None else None


#: The facts a native export can carry, by name. A harness declares the subset it
#: was measured to provide (`NativeTelemetry.provides`); the quality view reports
#: every other name as unsupported for that harness rather than as missing on each
#: row. "Unavailable" is what the provider cannot say; "missing" is what it did not
#: say this time.
CAPABILITIES = (
    "tool_duration",
    "tool_decision",
    "executed_input",
    "output_size",
    "output_content",
    "runtime_parent",
    "agent_identity",
    "turn_identity",
    "model_request",
    "model_cost",
    "first_token",
    "reasoning_tokens",
    "skill_activation",
    "compaction",
    "subagent",
    "provider_metrics",
    "guardian_review",
    "sandbox_outcome",
)


def provider_capabilities() -> dict[str, dict[str, str]]:
    """Per harness, whether each capability is measured, unmeasured, or impossible."""

    result: dict[str, dict[str, str]] = {}
    for name, harness in HARNESSES.items():
        contract = harness.native_telemetry
        if contract is None:
            result[name] = dict.fromkeys(CAPABILITIES, "no_native_telemetry")
            continue
        result[name] = {
            capability: "measured" if capability in contract.provides else "unmeasured"
            for capability in CAPABILITIES
        }
    return result


def provider_otel_env(
    backend: str,
    *,
    enabled: bool,
    ingress_url: str,
    session_id: str,
    secret: str,
) -> dict[str, str]:
    """Native exporter environment for providers with a verified local contract.

    Claude Code reads the OTLP configuration from its environment. The
    signal-specific logs endpoint is used verbatim (no `/v1/logs` is appended),
    which is why the ingress route carries that suffix itself.
    """

    contract = native_telemetry(backend)
    if not enabled or contract is None or contract.transport != "env":
        return {}
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "none",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": (
            f"{ingress_url.rstrip('/')}/api/telemetry/otlp/{session_id}/v1/logs"
        ),
        "OTEL_EXPORTER_OTLP_HEADERS": f"X-Mux-Hook-Secret={secret}",
        "OTEL_LOG_TOOL_DETAILS": "1",
    }


def provider_otel_args(
    backend: str,
    *,
    enabled: bool,
    ingress_url: str,
    session_id: str,
    secret: str,
) -> tuple[str, ...]:
    """Native CLI arguments where the provider configures OTLP outside env.

    Codex takes its exporters as configuration and posts to each endpoint exactly as
    written (measured on 0.153.0: twelve log batches and one 818 KB metrics batch on
    the configured paths, the header attached). The metrics exporter is configured
    only for a harness that declares `exports_metrics`; the reducer keeps the
    allow-listed metrics and drops the rest.
    """

    contract = native_telemetry(backend)
    if not enabled or contract is None or contract.transport != "config_arg":
        return ()
    base = f"{ingress_url.rstrip('/')}/api/telemetry/otlp/{session_id}"

    def exporter(signal: str) -> str:
        return (
            "{ otlp-http = { "
            f'endpoint = "{base}/v1/{signal}", protocol = "json", '
            f'headers = {{ "x-mux-hook-secret" = "{secret}" }}'
            " } }"
        )

    arguments = [
        "-c",
        'otel.environment="swe-mux"',
        "-c",
        "otel.log_user_prompt=false",
        "-c",
        f"otel.exporter={exporter('logs')}",
    ]
    if contract.exports_metrics:
        arguments.extend(("-c", f"otel.metrics_exporter={exporter('metrics')}"))
    return tuple(arguments)


def _any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in (
        "stringValue",
        "boolValue",
        "intValue",
        "doubleValue",
        "bytesValue",
    ):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        array = value["arrayValue"]
        return (
            [_any_value(item) for item in array.get("values", [])]
            if isinstance(array, dict)
            else []
        )
    if "kvlistValue" in value:
        kvlist = value["kvlistValue"]
        return _attributes(kvlist.get("values", [])) if isinstance(kvlist, dict) else {}
    return None


def _attributes(items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    result: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        result[str(item["key"])] = _any_value(item.get("value"))
    return result


def _timestamp(record: dict[str, Any]) -> float:
    raw = record.get("timeUnixNano") or record.get("observedTimeUnixNano")
    if raw is None:
        return time.time()
    try:
        return int(raw) / 1_000_000_000
    except (TypeError, ValueError):
        return 0.0


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.casefold() == "true":
            return True
        if value.casefold() == "false":
            return False
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_text(attributes: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _text(attributes.get(key))
        if text is not None:
            return text
    return None


def _content_metrics(value: Any, prefix: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    encoded = text.encode("utf-8", "replace")
    return {
        f"{prefix}_chars": len(text),
        f"{prefix}_bytes": len(encoded),
        f"{prefix}_sha256": hashlib.sha256(encoded).hexdigest(),
        f"{prefix}_measurement": "full",
    }


def _sized_metrics(
    content: Any, provider_bytes: Any, prefix: str, *, truncated: bool | None = None
) -> dict[str, Any]:
    """Measure content when it was shipped, else keep the provider's own size.

    A provider-reported size of a truncated body is the size of the truncation and
    is recorded as such rather than as the result's size.
    """

    metrics = _content_metrics(content, prefix)
    if not metrics:
        size = _integer(provider_bytes)
        if size is not None:
            metrics = {f"{prefix}_bytes": size, f"{prefix}_measurement": "provider_size_only"}
    if truncated and metrics:
        metrics[f"{prefix}_measurement"] = "provider_truncated"
    return metrics


def _event_name(attributes: dict[str, Any], body: Any) -> str:
    """The provider's event name with its vendor prefix removed.

    Claude puts the bare name in `event.name` and the prefixed one in the record body;
    Codex puts the prefixed name in `event.name`. Both collapse to the same token.
    """

    raw = _first_text(attributes, "event.name", "name")
    if raw is None:
        body_text = _text(_any_value(body)) or ""
        raw = body_text if body_text.startswith(("claude_code.", "codex.")) else ""
    for prefix in ("claude_code.", "codex."):
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return raw


def _agent_id(attributes: dict[str, Any]) -> str | None:
    explicit = _first_text(attributes, "agent_id", "agent.id", "gen_ai.agent.id")
    if explicit is not None:
        return explicit
    # Codex names the executing agent by path: `/root` for the conversation's own
    # agent, `/root/<name>` for a spawned one.
    name = _text(attributes.get("agent_name"))
    if name is None:
        return None
    if name == "/root":
        return "root"
    return name.removeprefix("/root/") or "root"


def _sequence(attributes: dict[str, Any]) -> int:
    for key in ("event.sequence", "tool_result_seq"):
        number = _integer(attributes.get(key))
        if number is not None:
            return number
    return 0


def _common(attributes: dict[str, Any], backend: str) -> dict[str, Any]:
    return {
        "backend": backend,
        "model": _first_text(attributes, "model", "gen_ai.request.model"),
        "harness_version": _first_text(attributes, "service.version", "app.version"),
        "turn_id": _first_text(attributes, "turn_id", "prompt.id", "interaction.id"),
        "agent_id": _agent_id(attributes),
        "parent_agent_id": _text(attributes.get("parent_agent_id")),
        "native_conversation_id": _text(attributes.get("conversation.id")),
        "provider_sequence": _sequence(attributes),
        "native_observed_at": _text(attributes.get("event.timestamp")),
        "parser_version": PARSER_VERSION,
    }


def _event(
    ts: float,
    session_id: str,
    event_type: str,
    attributes: dict[str, Any],
    payload: dict[str, Any],
) -> MuxEvent:
    reduced = {key: value for key, value in payload.items() if value is not None}
    return MuxEvent(
        ts,
        session_id,
        "otel",
        event_type,
        reduced,
        seq=_sequence(attributes),
        transient=True,
    )


def _invocation_layer(backend: str, call_id: str | None) -> str | None:
    contract = native_telemetry(backend)
    prefix = contract.runtime_call_id_prefix if contract is not None else None
    if prefix and call_id and call_id.startswith(prefix):
        return "runtime"
    return None


def _tool_result(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent:
    common = _common(attributes, backend)
    call_id = _first_text(attributes, "tool_use_id", "call_id")
    truncated = _boolean(attributes.get("output_truncated"))
    error = attributes.get("error")
    error_text = error if isinstance(error, str) else (json.dumps(error) if error else None)
    payload: dict[str, Any] = {
        **common,
        "tool": _first_text(attributes, "tool_name", "gen_ai.tool.name") or "tool",
        "call_id": call_id,
        "invocation_layer": _invocation_layer(backend, call_id),
        "server_name": _text(attributes.get("mcp_server")),
        "tool_namespace": _text(attributes.get("tool_namespace")),
        "success": _boolean(attributes.get("success")),
        "duration_ms": _number(attributes.get("duration_ms")),
        "error_type": _first_text(attributes, "error_type", "error.type")
        or ("provider_error" if error_text else None),
        "error_sha256": (
            hashlib.sha256(error_text.encode("utf-8", "replace")).hexdigest()
            if error_text
            else None
        ),
        "output_truncated": truncated,
        **_sized_metrics(
            attributes.get("output") or attributes.get("tool_result"),
            attributes.get("tool_result_size_bytes"),
            "output",
            truncated=truncated,
        ),
        **_sized_metrics(
            attributes.get("tool_input")
            or attributes.get("arguments")
            or attributes.get("tool_parameters"),
            attributes.get("tool_input_size_bytes"),
            "executed_input",
        ),
    }
    return _event(ts, session_id, "canonical_tool_result", attributes, payload)


def _tool_decision(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent:
    common = _common(attributes, backend)
    call_id = _first_text(attributes, "tool_use_id", "call_id")
    decision = (_text(attributes.get("decision")) or "").casefold()
    source = _first_text(attributes, "source", "decision_source")
    tool = _first_text(attributes, "tool_name", "gen_ai.tool.name") or "tool"
    if decision in _DENIED_DECISIONS:
        return _event(
            ts,
            session_id,
            "canonical_tool_result",
            attributes,
            {
                **common,
                "tool": tool,
                "call_id": call_id,
                "invocation_layer": _invocation_layer(backend, call_id),
                "success": False,
                "denied": True,
                "decision": decision,
                "decision_source": source,
            },
        )
    return _event(
        ts,
        session_id,
        "approval_resolved",
        attributes,
        {
            **common,
            "tool": tool,
            "call_id": call_id,
            "invocation_layer": _invocation_layer(backend, call_id),
            "decision": decision,
            "decision_source": source,
            "tool_source": _text(attributes.get("tool_source")),
        },
    )


#: Codex's sandbox verdicts (`codex-rs/core/src/tools/orchestrator.rs`): the
#: sandboxed attempt was refused and not retried, or it timed out or died by
#: signal inside the sandbox, or it failed there and the unsandboxed retry ran.
_SANDBOX_FAILURES = frozenset({"denied", "timed_out", "signal"})


def _sandbox_outcome(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent:
    """Reduce `codex.sandbox_outcome`, seen live on 0.153.0 when a write was refused.

    The event names the call and carries `outcome`, `initial_duration_ms`, and,
    for an escalated retry, `escalated_duration_ms` (`session_telemetry.rs`). A
    refusal is a denied result whose cause is known; a timeout or signal is a
    failed result with that cause; an escalation resolved an implicit approval to
    run outside the sandbox, so it is recorded as one. The provider's own
    `tool_result` for the call follows and fills what this does not carry.
    """

    common = _common(attributes, backend)
    call_id = _first_text(attributes, "call_id", "tool_use_id")
    outcome = (_text(attributes.get("outcome")) or "unknown").casefold()
    tool = _first_text(attributes, "tool_name", "gen_ai.tool.name") or "tool"
    base = {
        **common,
        "tool": tool,
        "call_id": call_id,
        "invocation_layer": _invocation_layer(backend, call_id),
        "sandbox_outcome": outcome,
        "sandbox_initial_duration_ms": _number(attributes.get("initial_duration_ms")),
        "sandbox_escalated_duration_ms": _number(attributes.get("escalated_duration_ms")),
    }
    if outcome in _SANDBOX_FAILURES:
        return _event(
            ts,
            session_id,
            "canonical_tool_result",
            attributes,
            {
                **base,
                "success": False,
                "denied": True if outcome == "denied" else None,
                "decision": outcome if outcome == "denied" else None,
                "decision_source": "sandbox" if outcome == "denied" else None,
                "error_type": f"sandbox_{outcome}",
                "duration_ms": _number(attributes.get("initial_duration_ms")),
            },
        )
    return _event(
        ts,
        session_id,
        "approval_resolved",
        attributes,
        {**base, "decision": outcome, "decision_source": "sandbox"},
    )


def _skill(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent | None:
    skill = _first_text(attributes, "skill.name", "skill_name")
    if skill is None:
        return None
    return _event(
        ts,
        session_id,
        "canonical_skill_invoked",
        attributes,
        {
            **_common(attributes, backend),
            "skill": skill,
            "invocation_id": _text(attributes.get("invocation_id")),
            "invocation_trigger": _first_text(
                attributes, "invocation_trigger", "invocation_type"
            ),
            "skill_source": _text(attributes.get("skill.source")),
            "skill_scope": _text(attributes.get("skill_scope")),
            "plugin_id": _first_text(attributes, "plugin.name", "plugin_id"),
            "plugin_version": _text(attributes.get("plugin.version")),
        },
    )


def _compaction(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent:
    return _event(
        ts,
        session_id,
        "context_compacted",
        attributes,
        {
            **_common(attributes, backend),
            "compaction_id": _first_text(attributes, "compaction_id")
            or (str(attributes["event.sequence"]) if "event.sequence" in attributes else None),
            "trigger": _text(attributes.get("trigger")),
            "success": _boolean(attributes.get("success")),
            "duration_ms": _number(attributes.get("duration_ms")),
            "tokens_before": _integer(attributes.get("pre_tokens")),
            "tokens_after": _integer(attributes.get("post_tokens")),
        },
    )


def _subagent(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent:
    return _event(
        ts,
        session_id,
        "subagent_activity",
        attributes,
        {
            **_common(attributes, backend),
            "kind": "completed",
            "agent_type": _text(attributes.get("agent_type")),
            "duration_ms": _number(attributes.get("duration_ms")),
        },
    )


def _api_request(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent | None:
    common = _common(attributes, backend)
    success = _boolean(attributes.get("success"))
    endpoint = _text(attributes.get("endpoint"))
    contract = native_telemetry(backend)
    if contract is not None and contract.http_requests_are_plumbing and success is not False:
        # For Codex, `api_request` is an HTTP call (`/models`, `/responses`) and the
        # model traffic completes as `sse_event`. A successful HTTP call is not a
        # model request; a failed one is the only failure evidence there is.
        return None
    status = _integer(attributes.get("http.response.status_code"))
    return _event(
        ts,
        session_id,
        "canonical_model_request",
        attributes,
        {
            **common,
            "request_id": _first_text(attributes, "request_id", "gen_ai.response.id"),
            "client_request_id": _text(attributes.get("client_request_id")),
            "query_source": _text(attributes.get("query_source")),
            "endpoint": endpoint,
            "duration_ms": _number(attributes.get("duration_ms")),
            "success": True if success is None else success,
            "attempts": _integer(attributes.get("attempt")),
            "input_tokens": _integer(attributes.get("input_tokens")),
            "output_tokens": _integer(attributes.get("output_tokens")),
            "cache_read_tokens": _integer(attributes.get("cache_read_tokens")),
            "cache_write_tokens": _integer(
                attributes.get("cache_creation_tokens")
                if attributes.get("cache_creation_tokens") is not None
                else attributes.get("cache_write_tokens")
            ),
            "cost_usd": _number(attributes.get("cost_usd")),
            "effort": _text(attributes.get("effort")),
            "speed": _text(attributes.get("speed")),
            "error_type": (
                None
                if success is not False
                else _first_text(attributes, "error_type", "error_name")
                or (f"http_{status}" if status is not None else "provider_error")
            ),
        },
    )


def _api_error(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent:
    error = attributes.get("error")
    error_text = error if isinstance(error, str) else (json.dumps(error) if error else None)
    return _event(
        ts,
        session_id,
        "canonical_model_request",
        attributes,
        {
            **_common(attributes, backend),
            "request_id": _text(attributes.get("request_id")),
            "query_source": _text(attributes.get("query_source")),
            "duration_ms": _number(attributes.get("duration_ms")),
            "success": False,
            "attempts": _integer(attributes.get("attempt")),
            "error_type": _first_text(attributes, "error_type", "error_name")
            or ("provider_error" if error_text else None),
            "error_sha256": (
                hashlib.sha256(error_text.encode("utf-8", "replace")).hexdigest()
                if error_text
                else None
            ),
        },
    )


def _sse_event(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent | None:
    if _text(attributes.get("event.kind")) != "response.completed":
        return None
    return _event(
        ts,
        session_id,
        "canonical_model_request",
        attributes,
        {
            **_common(attributes, backend),
            "success": True,
            "input_tokens": _integer(attributes.get("input_token_count")),
            "output_tokens": _integer(attributes.get("output_token_count")),
            "cache_read_tokens": _integer(attributes.get("cached_token_count")),
            "cache_write_tokens": _integer(attributes.get("cache_write_token_count")),
            "reasoning_tokens": _integer(attributes.get("reasoning_token_count")),
            "first_token_ms": _number(attributes.get("ttft_ms")),
            "effort": _text(attributes.get("model_reasoning_effort")),
        },
    )


def _websocket_request(
    ts: float, session_id: str, backend: str, attributes: dict[str, Any]
) -> MuxEvent | None:
    if _boolean(attributes.get("success")) is not False:
        return None
    return _event(
        ts,
        session_id,
        "canonical_model_request",
        attributes,
        {
            **_common(attributes, backend),
            "success": False,
            "duration_ms": _number(attributes.get("duration_ms")),
            "error_type": _first_text(attributes, "error_type", "error.type")
            or "websocket_error",
        },
    )


_REDUCERS = {
    "tool_result": _tool_result,
    "tool_decision": _tool_decision,
    "sandbox_outcome": _sandbox_outcome,
    "skill_activated": _skill,
    "skill_invocation": _skill,
    "compaction": _compaction,
    "subagent_completed": _subagent,
    "api_request": _api_request,
    "api_error": _api_error,
    "sse_event": _sse_event,
    "websocket_request": _websocket_request,
}


@dataclass
class OtlpReduction:
    """What one batch reduced to, plus the names it carried for drift accounting."""

    events: list[MuxEvent] = field(default_factory=list)
    #: `(event name, recognised)` -> occurrences in this batch. A recognised name
    #: whose reducer returned nothing still counts as recognised.
    signatures: dict[tuple[str, bool], int] = field(default_factory=dict)
    harness_version: str | None = None

    def note(self, name: str, recognised: bool) -> None:
        key = (name, recognised)
        self.signatures[key] = self.signatures.get(key, 0) + 1


def _resource_attributes(resource: Any) -> dict[str, Any]:
    block = resource.get("resource") if isinstance(resource, dict) else None
    return _attributes(block.get("attributes", [])) if isinstance(block, dict) else {}


def otlp_log_reduction(payload: Any, *, session_id: str, backend: str) -> OtlpReduction:
    """Reduce OTLP logs to safe canonical observations and discard raw content."""

    if not isinstance(payload, dict):
        raise ValueError("OTLP payload must be an object")
    resources = payload.get("resourceLogs")
    if not isinstance(resources, list):
        raise ValueError("OTLP payload must contain resourceLogs")
    reduction = OtlpReduction()
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        resource_attributes = _resource_attributes(resource)
        if reduction.harness_version is None:
            reduction.harness_version = _first_text(
                resource_attributes, "service.version", "app.version"
            )
        scopes = resource.get("scopeLogs")
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict):
                continue
            records = scope.get("logRecords")
            for record in records if isinstance(records, list) else []:
                if not isinstance(record, dict):
                    continue
                attributes = {**resource_attributes, **_attributes(record.get("attributes"))}
                name = _event_name(attributes, record.get("body"))
                if reduction.harness_version is None:
                    reduction.harness_version = _text(attributes.get("app.version"))
                if not name:
                    # A plain log line (Codex writes "flushing OTEL metrics" this
                    # way) is not a provider event and not drift.
                    reduction.note("<log line>", True)
                    continue
                reducer = _REDUCERS.get(name)
                if reducer is None:
                    reduction.note(name, name in _KNOWN_IGNORED_EVENTS)
                    continue
                reduction.note(name, True)
                normalized = reducer(_timestamp(record), session_id, backend, attributes)
                if normalized is not None:
                    reduction.events.append(normalized)
    return reduction


def otlp_log_events(payload: Any, *, session_id: str, backend: str) -> list[MuxEvent]:
    return otlp_log_reduction(payload, session_id=session_id, backend=backend).events


#: Metrics kept as aggregated provider self-reports beside the canonical entities.
#: They carry no call identity, so they never become entities; what they are good
#: for is a denominator the provider computed itself (`codex.tool.call` against the
#: ledger's own count of that run's calls). Everything else Codex exports - startup
#: phases, cache hits, sqlite timings - is counted as a recognised signature and
#: dropped. Measured on Codex CLI 0.153.0.
METRIC_ALLOWLIST = frozenset(
    {
        "codex.tool.call",
        "codex.tool.call.duration_ms",
        "codex.turn.tool.call",
        "codex.turn.token_usage",
        "codex.turn.e2e_duration_ms",
        "codex.turn.ttft.duration_ms",
        "codex.conversation.turn.count",
        "codex.guardian.review",
        "codex.guardian.review.duration_ms",
        "codex.hooks.run",
        "codex.hooks.run.duration_ms",
        "codex.thread.skills.enabled_total",
        "codex.thread.skills.kept_total",
        "codex.skill.injected",
        "codex.rollout.size_bytes",
    }
)
#: Point attributes kept beside a metric: outcome and dimension names only. Never
#: an identity (`user.*`, `auth_mode`) and never a body.
METRIC_ATTRIBUTES = frozenset(
    {
        "tool",
        "success",
        "command_category",
        "sandbox",
        "sandbox_policy",
        "session_source",
        "token_type",
        "decision",
        "outcome",
        "risk_level",
        "action",
        "terminal_status",
        "hook_name",
        "status",
        "kind",
        "event",
        "model",
        "skill",
        "invoke_type",
        "guardian_model",
    }
)


def _metric_agent(attributes: dict[str, Any]) -> str:
    source = _text(attributes.get("session_source"))
    if source and source.startswith("subagent"):
        return source
    return "root"


def _nanos(value: Any) -> float | None:
    try:
        return int(value) / 1_000_000_000 if value is not None else None
    except (TypeError, ValueError):
        return None


def _skill_counter(
    ts: float,
    session_id: str,
    backend: str,
    attributes: dict[str, Any],
    point: dict[str, Any],
    temporality: str,
) -> MuxEvent | None:
    skill = _text(attributes.get("skill"))
    count = _integer(point.get("asInt") or point.get("asDouble"))
    if skill is None or not count:
        return None
    invoke_type = _text(attributes.get("invoke_type")) or "unknown"
    series_id = hashlib.sha256(f"{session_id}:{skill}:{invoke_type}".encode()).hexdigest()
    return _event(
        ts,
        session_id,
        "canonical_skill_invoked",
        attributes,
        {
            **_common(attributes, backend),
            "skill": skill,
            "invocation_id": f"metric:{series_id}:{point.get('timeUnixNano')}",
            "invocation_trigger": invoke_type,
            "count": count,
            "metric_temporality": temporality,
            "metric_series_id": series_id,
            "metric_start_time": point.get("startTimeUnixNano"),
        },
    )


def otlp_metric_reduction(payload: Any, *, session_id: str, backend: str) -> OtlpReduction:
    """Reduce OTLP metrics to aggregated provider self-reports; drop the rest.

    A counter or histogram point becomes one `provider_metric` observation carrying
    its allow-listed dimensions, count, sum, min, and max. `codex.skill.injected`
    additionally becomes a skill invocation, because it is the only metric that
    names an activation the log stream does not.
    """

    if not isinstance(payload, dict):
        raise ValueError("OTLP payload must be an object")
    resources = payload.get("resourceMetrics")
    if not isinstance(resources, list):
        raise ValueError("OTLP payload must contain resourceMetrics")
    reduction = OtlpReduction()
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        resource_attributes = _resource_attributes(resource)
        if reduction.harness_version is None:
            reduction.harness_version = _first_text(
                resource_attributes, "service.version", "app.version"
            )
        scopes = resource.get("scopeMetrics")
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict):
                continue
            metrics = scope.get("metrics")
            for metric in metrics if isinstance(metrics, list) else []:
                if not isinstance(metric, dict):
                    continue
                metric_name = str(metric.get("name") or "<unnamed>")
                signature = f"metric:{metric_name}"
                if metric_name not in METRIC_ALLOWLIST:
                    reduction.note(signature, True)
                    continue
                reduction.note(signature, True)
                kind = next((k for k in ("sum", "histogram", "gauge") if k in metric), None)
                aggregate = metric.get(kind) if kind else None
                if not isinstance(aggregate, dict):
                    continue
                temporality = (
                    "cumulative"
                    if str(aggregate.get("aggregationTemporality")) == "2"
                    else "delta"
                )
                points = aggregate.get("dataPoints")
                for point in points if isinstance(points, list) else []:
                    if not isinstance(point, dict):
                        continue
                    attributes = {**resource_attributes, **_attributes(point.get("attributes"))}
                    if reduction.harness_version is None:
                        reduction.harness_version = _text(attributes.get("app.version"))
                    ts = _timestamp(point)
                    if metric_name == "codex.skill.injected":
                        skill_event = _skill_counter(
                            ts, session_id, backend, attributes, point, temporality
                        )
                        if skill_event is not None:
                            reduction.events.append(skill_event)
                    kept = {
                        key: value
                        for key, value in attributes.items()
                        if key in METRIC_ATTRIBUTES and value is not None
                    }
                    if kind == "histogram":
                        count = _integer(point.get("count"))
                        total = _number(point.get("sum"))
                        low = _number(point.get("min"))
                        high = _number(point.get("max"))
                    else:
                        value = _number(point.get("asInt") or point.get("asDouble"))
                        count, total, low, high = None, value, None, None
                    reduction.events.append(
                        _event(
                            ts,
                            session_id,
                            "provider_metric",
                            attributes,
                            {
                                **_common(attributes, backend),
                                "agent_id": _metric_agent(attributes),
                                "metric": metric_name,
                                "kind": kind,
                                "temporality": temporality,
                                "attributes": kept,
                                "count": count,
                                "sum": total,
                                "min": low,
                                "max": high,
                                "started_at": _nanos(point.get("startTimeUnixNano")),
                                "native_observed_at": _text(point.get("timeUnixNano")),
                            },
                        )
                    )
    return reduction


def otlp_metric_events(payload: Any, *, session_id: str, backend: str) -> list[MuxEvent]:
    return otlp_metric_reduction(payload, session_id=session_id, backend=backend).events


def otlp_reduction(payload: Any, *, session_id: str, backend: str) -> OtlpReduction:
    if isinstance(payload, dict) and "resourceLogs" in payload:
        return otlp_log_reduction(payload, session_id=session_id, backend=backend)
    if isinstance(payload, dict) and "resourceMetrics" in payload:
        return otlp_metric_reduction(payload, session_id=session_id, backend=backend)
    raise ValueError("OTLP payload must contain resourceLogs or resourceMetrics")


def otlp_events(payload: Any, *, session_id: str, backend: str) -> list[MuxEvent]:
    return otlp_reduction(payload, session_id=session_id, backend=backend).events
