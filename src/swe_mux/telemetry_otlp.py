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
    }
)


def native_telemetry(backend: str) -> NativeTelemetry | None:
    """The registry's measured OTLP contract for this harness, or None."""

    harness = HARNESSES.get(backend)
    return harness.native_telemetry if harness is not None else None


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

    Codex takes its exporter as configuration, and posts to the endpoint exactly as
    written (measured on 0.153.0: twelve batches on the configured path, the header
    attached). Only the log exporter is configured; the metrics exporter key is not
    verified against a real run and is deliberately left unset.
    """

    contract = native_telemetry(backend)
    if not enabled or contract is None or contract.transport != "config_arg":
        return ()
    endpoint = f"{ingress_url.rstrip('/')}/api/telemetry/otlp/{session_id}/v1/logs"
    exporter = (
        "otel.exporter={ otlp-http = { "
        f'endpoint = "{endpoint}", protocol = "json", '
        f'headers = {{ "x-mux-hook-secret" = "{secret}" }}'
        " } }"
    )
    return (
        "-c",
        'otel.environment="swe-mux"',
        "-c",
        "otel.log_user_prompt=false",
        "-c",
        exporter,
    )


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


def otlp_metric_reduction(payload: Any, *, session_id: str, backend: str) -> OtlpReduction:
    """Reduce attributable counter points; aggregated counts remain aggregated.

    Only `codex.skill.injected` is understood. No provider is configured to export
    metrics to the ingress yet, so this reducer is exercised by its unit tests and
    stands ready rather than being measured against a live run.
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
        scopes = resource.get("scopeMetrics")
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict):
                continue
            metrics = scope.get("metrics")
            for metric in metrics if isinstance(metrics, list) else []:
                if not isinstance(metric, dict):
                    continue
                metric_name = str(metric.get("name") or "<unnamed>")
                if metric_name != "codex.skill.injected":
                    reduction.note(f"metric:{metric_name}", False)
                    continue
                reduction.note(f"metric:{metric_name}", True)
                aggregate = metric.get("sum")
                points = aggregate.get("dataPoints") if isinstance(aggregate, dict) else []
                temporality_value = (
                    aggregate.get("aggregationTemporality")
                    if isinstance(aggregate, dict)
                    else None
                )
                temporality = "cumulative" if str(temporality_value) == "2" else "delta"
                for point in points if isinstance(points, list) else []:
                    if not isinstance(point, dict):
                        continue
                    attributes = {**resource_attributes, **_attributes(point.get("attributes"))}
                    skill = _text(attributes.get("skill"))
                    count = _integer(point.get("asInt") or point.get("asDouble"))
                    if skill is None or not count:
                        continue
                    invoke_type = _text(attributes.get("invoke_type")) or "unknown"
                    series_id = hashlib.sha256(
                        f"{session_id}:{skill}:{invoke_type}".encode()
                    ).hexdigest()
                    reduction.events.append(
                        _event(
                            _timestamp(point),
                            session_id,
                            "canonical_skill_invoked",
                            attributes,
                            {
                                **_common(attributes, backend),
                                "skill": skill,
                                "invocation_id": (
                                    f"metric:{series_id}:{point.get('timeUnixNano')}"
                                ),
                                "invocation_trigger": invoke_type,
                                "count": count,
                                "metric_temporality": temporality,
                                "metric_series_id": series_id,
                                "metric_start_time": point.get("startTimeUnixNano"),
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
