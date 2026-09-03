from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.routes.agent_ingress import telemetry_otlp_logs
from swe_mux.telemetry_otlp import (
    CONTENT_ATTRIBUTES,
    IDENTITY_ATTRIBUTES,
    PARSER_VERSION,
    otlp_events,
    otlp_log_events,
    otlp_log_reduction,
    provider_otel_args,
    provider_otel_env,
)

FIXTURES = Path(__file__).parent / "fixtures" / "telemetry"
#: Real exports, captured through the daemon's own ingress and then scrubbed.
#: Identities became fixed placeholders and content became same-length filler,
#: so the attribute *names* and shapes are exactly what the CLI sent.
CLAUDE_CAPTURE = FIXTURES / "otlp-claude-2.1.259.json"
CODEX_CAPTURE = FIXTURES / "otlp-codex-0.153.0.json"


def attribute(key: str, value: object) -> dict[str, object]:
    if isinstance(value, bool):
        wrapped = {"boolValue": value}
    elif isinstance(value, int):
        wrapped = {"intValue": str(value)}
    else:
        wrapped = {"stringValue": str(value)}
    return {"key": key, "value": wrapped}


def batch(*attributes: dict[str, object]) -> dict[str, object]:
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": []},
                "scopeLogs": [
                    {
                        "logRecords": [
                            {
                                "timeUnixNano": "1788000000000000000",
                                "attributes": list(attributes),
                            }
                        ]
                    }
                ],
            }
        ]
    }


def _capture(path: Path) -> list[dict[str, Any]]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def _reduce_capture(path: Path, backend: str) -> tuple[list[Any], dict[tuple[str, bool], int]]:
    events: list[Any] = []
    signatures: dict[tuple[str, bool], int] = {}
    for payload in _capture(path):
        reduction = otlp_log_reduction(payload, session_id="session-1", backend=backend)
        events.extend(reduction.events)
        for key, count in reduction.signatures.items():
            signatures[key] = signatures.get(key, 0) + count
    return events, signatures


def test_tool_result_is_reduced_before_it_reaches_storage() -> None:
    payload = batch(
        attribute("event.name", "claude_code.tool_result"),
        attribute("tool_name", "Bash"),
        attribute("tool_use_id", "call-1"),
        attribute("success", True),
        attribute("duration_ms", 125),
        attribute("arguments", '{"path":"private.py"}'),
        attribute("output", "secret tool output"),
    )
    event = otlp_log_events(payload, session_id="session-1", backend="claude")[0]

    assert event.type == "canonical_tool_result"
    assert event.payload["call_id"] == "call-1"
    assert event.payload["duration_ms"] == 125
    assert event.payload["output_chars"] == len("secret tool output")
    assert event.payload["output_sha256"]
    assert event.payload["output_measurement"] == "full"
    assert event.payload["executed_input_sha256"]
    assert event.payload["executed_input_measurement"] == "full"
    assert event.payload["parser_version"] == PARSER_VERSION
    assert "private.py" not in str(event.payload)
    assert "secret tool output" not in str(event.payload)


def test_denial_and_skill_activation_keep_native_identity() -> None:
    denied = otlp_log_events(
        batch(
            attribute("event.name", "codex.tool_decision"),
            attribute("tool_name", "exec_command"),
            attribute("call_id", "call-2"),
            attribute("decision", "denied"),
            attribute("source", "user"),
        ),
        session_id="session-1",
        backend="codex",
    )[0]
    skill = otlp_log_events(
        batch(
            attribute("event.name", "claude_code.skill_activated"),
            attribute("skill.name", "documentation"),
            attribute("invocation_trigger", "claude-proactive"),
            attribute("skill.source", "userSettings"),
        ),
        session_id="session-1",
        backend="claude",
    )[0]

    assert denied.payload["denied"] is True
    assert denied.payload["call_id"] == "call-2"
    assert denied.payload["decision_source"] == "user"
    assert skill.payload["skill"] == "documentation"
    assert skill.payload["invocation_trigger"] == "claude-proactive"


def test_unknown_logs_are_ignored_and_reported_rather_than_guessed() -> None:
    payload = batch(attribute("event.name", "future.provider.event"))
    reduction = otlp_log_reduction(payload, session_id="session-1", backend="claude")
    assert reduction.events == []
    assert reduction.signatures == {("future.provider.event", False): 1}


def test_api_request_keeps_timing_and_tokens_without_prompt_or_error_text() -> None:
    request = otlp_log_events(
        batch(
            attribute("event.name", "claude_code.api_request"),
            attribute("request_id", "request-1"),
            attribute("prompt.id", "turn-1"),
            attribute("model", "claude-opus-5"),
            attribute("duration_ms", 900),
            attribute("input_tokens", 1200),
            attribute("output_tokens", 80),
            attribute("cache_read_tokens", 700),
        ),
        session_id="session-1",
        backend="claude",
    )[0]
    error = otlp_log_events(
        batch(
            attribute("event.name", "claude_code.api_error"),
            attribute("error", "private provider failure"),
            attribute("attempt", 3),
        ),
        session_id="session-1",
        backend="claude",
    )[0]

    assert request.type == "canonical_model_request"
    assert request.payload["turn_id"] == "turn-1"
    assert request.payload["input_tokens"] == 1200
    assert error.payload["attempts"] == 3
    assert error.payload["error_sha256"]
    assert "private provider failure" not in str(error.payload)


def test_codex_skill_counter_preserves_attributable_delta_count() -> None:
    payload = {
        "resourceMetrics": [
            {
                "resource": {"attributes": [attribute("app.version", "0.153.0")]},
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": "codex.skill.injected",
                                "sum": {
                                    "dataPoints": [
                                        {
                                            "timeUnixNano": "1788000000000000000",
                                            "asInt": "3",
                                            "attributes": [
                                                attribute("skill", "openai-docs"),
                                                attribute("invoke_type", "implicit"),
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }

    event = otlp_events(payload, session_id="session-1", backend="codex")[0]
    assert event.type == "canonical_skill_invoked"
    assert event.payload["skill"] == "openai-docs"
    assert event.payload["count"] == 3
    assert event.payload["harness_version"] == "0.153.0"


def test_only_enabled_providers_with_a_verified_contract_receive_exporter_env() -> None:
    assert provider_otel_env(
        "codex",
        enabled=True,
        ingress_url="http://127.0.0.1:8765",
        session_id="session-1",
        secret="secret",
    ) == {}
    assert provider_otel_env(
        "claude",
        enabled=False,
        ingress_url="http://127.0.0.1:8765",
        session_id="session-1",
        secret="secret",
    ) == {}
    enabled = provider_otel_env(
        "claude",
        enabled=True,
        ingress_url="http://127.0.0.1:8765",
        session_id="session-1",
        secret="secret",
    )
    assert enabled["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"].endswith(
        "/api/telemetry/otlp/session-1/v1/logs"
    )
    assert enabled["OTEL_EXPORTER_OTLP_HEADERS"] == "X-Mux-Hook-Secret=secret"

    codex = provider_otel_args(
        "codex",
        enabled=True,
        ingress_url="http://127.0.0.1:8765",
        session_id="session-1",
        secret="secret",
    )
    assert codex[:4] == (
        "-c",
        'otel.environment="swe-mux"',
        "-c",
        "otel.log_user_prompt=false",
    )
    assert "http://127.0.0.1:8765/api/telemetry/otlp/session-1/v1/logs" in codex[-1]
    assert '"x-mux-hook-secret" = "secret"' in codex[-1]


# -- measured provider contracts ---------------------------------------------------


def test_the_captures_carry_the_identities_the_reducer_must_drop() -> None:
    """The fixtures are only evidence if the scrubbed identity keys were really there."""

    for path in (CLAUDE_CAPTURE, CODEX_CAPTURE):
        keys_seen: set[str] = set()
        for payload in _capture(path):
            for resource in payload["resourceLogs"]:
                for scope in resource["scopeLogs"]:
                    for record in scope["logRecords"]:
                        keys_seen.update(item["key"] for item in record["attributes"])
        assert {"user.email", "user.account_id"} <= keys_seen, path.name
        assert keys_seen & CONTENT_ATTRIBUTES, path.name


def test_no_identity_or_content_attribute_survives_reduction() -> None:
    for path, backend in ((CLAUDE_CAPTURE, "claude"), (CODEX_CAPTURE, "codex")):
        events, _ = _reduce_capture(path, backend)
        assert events, path.name
        for event in events:
            leaked = set(event.payload) & (IDENTITY_ATTRIBUTES | CONTENT_ATTRIBUTES)
            assert not leaked, (path.name, event.type, leaked)
            text = json.dumps(event.payload)
            assert "operator@example.invalid" not in text
            assert "account-id-redacted" not in text
            assert "organization-id-redacted" not in text


def test_claude_2_1_259_export_reduces_to_calls_decisions_and_requests() -> None:
    events, signatures = _reduce_capture(CLAUDE_CAPTURE, "claude")
    by_type: dict[str, list[Any]] = {}
    for event in events:
        by_type.setdefault(event.type, []).append(event)

    assert {name for (name, _recognised) in signatures} >= {
        "tool_result",
        "tool_decision",
        "api_request",
        "user_prompt",
        "assistant_response",
    }
    assert all(recognised for (_name, recognised) in signatures), signatures
    [result] = by_type["canonical_tool_result"]
    assert result.payload["tool"] == "Read"
    assert result.payload["call_id"].startswith("toolu_")
    assert result.payload["success"] is True
    assert result.payload["duration_ms"] == 7
    assert result.payload["turn_id"], "prompt.id is the Claude turn identity"
    assert result.payload["harness_version"] == "2.1.259"
    assert result.payload["provider_sequence"] > 0
    # Claude ships the arguments (hashed here) and only the result's size.
    assert result.payload["executed_input_measurement"] == "full"
    assert result.payload["executed_input_bytes"] == 88
    assert result.payload["executed_input_sha256"]
    assert result.payload["output_measurement"] == "provider_size_only"
    assert result.payload["output_bytes"] == 14
    assert "output_sha256" not in result.payload
    [decision] = by_type["approval_resolved"]
    assert decision.payload["decision"] == "accept"
    assert decision.payload["decision_source"] == "config"
    assert decision.payload["call_id"] == result.payload["call_id"]
    requests = by_type["canonical_model_request"]
    assert len(requests) == 3
    assert all(item.payload["request_id"].startswith("req_") for item in requests)
    assert all(item.payload["success"] is True for item in requests)
    assert {item.payload["query_source"] for item in requests} >= {"generate_session_title"}
    assert all(item.payload["cost_usd"] is not None for item in requests)
    assert all(item.payload["input_tokens"] is not None for item in requests)


def test_codex_0_153_0_export_separates_nested_execution_from_model_calls() -> None:
    events, signatures = _reduce_capture(CODEX_CAPTURE, "codex")
    by_type: dict[str, list[Any]] = {}
    for event in events:
        by_type.setdefault(event.type, []).append(event)

    assert all(recognised for (_name, recognised) in signatures), signatures
    results = by_type["canonical_tool_result"]
    assert len(results) == 4
    layers = {
        (item.payload["tool"], item.payload.get("invocation_layer")) for item in results
    }
    # `exec` is the model-facing code-mode call; `exec_command` under an `exec-`
    # call id is the execution it dispatched. They are two layers, not four peers.
    assert layers == {("exec", None), ("exec_command", "runtime")}
    for item in results:
        assert item.payload["success"] is True
        assert item.payload["duration_ms"] > 0
        assert item.payload["agent_id"] == "root"
        assert item.payload["harness_version"] == "0.153.0"
        assert item.payload["native_conversation_id"]
        assert item.payload["output_truncated"] is False
        assert item.payload["output_measurement"] == "full"
        assert item.payload["executed_input_measurement"] == "full"
    decisions = by_type["approval_resolved"]
    assert {item.payload["decision"] for item in decisions} == {"approved"}
    assert {item.payload["decision_source"] for item in decisions} == {
        "Config",
        "AutomatedReviewer",
    }
    requests = by_type["canonical_model_request"]
    # Six `response.completed` completions; the `/models` HTTP calls and the
    # successful websocket sends are not model requests and are not counted.
    assert len(requests) == 6
    assert all(item.payload["success"] is True for item in requests)
    assert all("request_id" not in item.payload for item in requests)
    assert {item.payload["model"] for item in requests} == {"gpt-5.6-sol", "codex-auto-review"}
    assert any(item.payload.get("first_token_ms") for item in requests)
    assert any(item.payload.get("reasoning_tokens") for item in requests)
    assert all(item.payload["input_tokens"] > 0 for item in requests)
    # Two conversations - the thread and its automated reviewer - stay distinct.
    assert len({item.payload["native_conversation_id"] for item in requests}) == 2


def test_codex_http_and_websocket_failures_become_failed_model_requests() -> None:
    failed_http = otlp_log_events(
        batch(
            attribute("event.name", "codex.api_request"),
            attribute("endpoint", "/responses"),
            attribute("success", False),
            attribute("http.response.status_code", 429),
            attribute("duration_ms", 40),
        ),
        session_id="session-1",
        backend="codex",
    )
    failed_socket = otlp_log_events(
        batch(
            attribute("event.name", "codex.websocket_request"),
            attribute("success", "false"),
            attribute("duration_ms", 12),
        ),
        session_id="session-1",
        backend="codex",
    )
    ok_http = otlp_log_events(
        batch(
            attribute("event.name", "codex.api_request"),
            attribute("endpoint", "/models"),
            attribute("success", True),
        ),
        session_id="session-1",
        backend="codex",
    )

    assert failed_http[0].payload["error_type"] == "http_429"
    assert failed_http[0].payload["success"] is False
    assert failed_socket[0].payload["error_type"] == "websocket_error"
    assert ok_http == []


async def test_otlp_ingress_is_session_authenticated_and_queues_reduced_events() -> None:
    accepted: list[object] = []
    noted: list[dict[str, Any]] = []
    session = SimpleNamespace(
        record=SimpleNamespace(id="session-1", backend="claude"),
        hook_secret="secret",
    )
    app = web.Application()
    app[keys.SESSIONS] = SimpleNamespace(resolve=lambda _sid: session)
    app[keys.CANONICAL_TELEMETRY] = SimpleNamespace(
        enqueue_provider_event=lambda event: not accepted.append(event),
        note_parser_signatures=lambda **kwargs: noted.append(kwargs),
    )
    app.router.add_post("/api/telemetry/otlp/{sid}/v1/logs", telemetry_otlp_logs)
    payload = batch(
        attribute("event.name", "claude_code.tool_result"),
        attribute("tool_name", "Read"),
        attribute("tool_use_id", "call-1"),
        attribute("success", True),
    )

    async with TestClient(TestServer(app)) as client:
        refused = await client.post(
            "/api/telemetry/otlp/session-1/v1/logs",
            json=payload,
            headers={"X-Mux-Hook-Secret": "wrong"},
        )
        response = await client.post(
            "/api/telemetry/otlp/session-1/v1/logs",
            json=payload,
            headers={"X-Mux-Hook-Secret": "secret"},
        )
        response_payload = await response.json()

    assert refused.status == 403
    assert response.status == 200
    assert response_payload["partialSuccess"]["rejectedLogRecords"] == 0
    assert len(accepted) == 1
    assert noted == [
        {
            "backend": "claude",
            "harness_version": None,
            "parser_version": PARSER_VERSION,
            "signatures": {("tool_result", True): 1},
        }
    ]
