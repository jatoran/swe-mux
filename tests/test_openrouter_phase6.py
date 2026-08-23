from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.llm_endpoint import custom_endpoint, openrouter_endpoint
from swe_mux.openrouter import (
    MAX_RESPONSE_BYTES,
    MAX_RETRY_SLEEP_SECONDS,
    MAX_SESSION_ID_CHARS,
    RETRY_ATTEMPTS,
    OpenRouterClient,
    OpenRouterError,
    _retry_after,
    apply_session_routing,
    cache_discount_usd,
    cache_saving_usd,
    cache_stable_message,
    cache_write_prompt_tokens,
    cached_prompt_tokens,
    marks_cache_breakpoints,
)
from swe_mux.secret_store import PlatformSecretStore
from swe_mux.server import automation_provider_key


class MemorySecrets:
    def __init__(self, value: str = "secret-key") -> None:
        self.value = value

    def get(self, _name: str) -> str | None:
        return self.value

    def set(self, _name: str, value: str) -> None:
        self.value = value

    def clear(self, _name: str) -> None:
        self.value = ""

    def status(self, _name: str) -> dict[str, object]:
        return {"configured": bool(self.value)}


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):  # type: ignore[no-untyped-def]
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class FakeResponse:
    def __init__(self, status: int, value: Any, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.content = FakeContent(body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)

    async def close(self) -> None:
        return None


class RejectingProvider:
    async def test_key(
        self, _candidate: str | None = None, *, endpoint: Any = None
    ) -> dict[str, Any]:
        raise OpenRouterError("OpenRouter request failed with HTTP 401")


class VerificationStoreStub:
    """Just the two provider-verification calls the key route reaches."""

    def __init__(self) -> None:
        self.cleared: list[str] = []

    async def provider_verification(self, provider: str) -> dict[str, Any] | None:
        return None

    async def clear_provider_verification(self, provider: str) -> None:
        self.cleared.append(provider)


async def test_openrouter_fixed_origin_retry_filter_and_redaction() -> None:
    session = FakeSession(
        [
            FakeResponse(429, {"error": "do not expose provider body"}),
            FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "vendor/structured",
                            "name": "Structured",
                            "supported_parameters": ["response_format"],
                            "architecture": {"modality": "text->text"},
                            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        },
                        {
                            "id": "vendor/image",
                            "supported_parameters": ["response_format"],
                            "architecture": {"modality": "text->image"},
                        },
                        {
                            "id": "vendor/unstructured",
                            "architecture": {"modality": "text->text"},
                        },
                    ]
                },
            ),
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]

    assert [item["id"] for item in await client.models()] == ["vendor/structured"]
    assert len(session.requests) == 2
    assert all(url == "https://openrouter.ai/api/v1/models" for _, url, _ in session.requests)
    assert session.requests[0][2]["allow_redirects"] is False
    assert session.requests[0][2]["headers"]["Authorization"] == "Bearer secret-key"


async def test_rate_limited_request_rides_out_a_burst_then_gives_up_bounded() -> None:
    """Observers collide at turn boundaries, so 429 arrives in bursts, not singly.

    The previous 0.25s/0.5s pair was shorter than the burst and made 429 effectively
    fatal — 20 of 70 titler calls lost in a measured day. Retries have to outlast a
    burst without becoming unbounded, and must honour the server's own hint when it
    sends one.
    """
    burst = FakeSession(
        [
            FakeResponse(429, {"error": "rate limited"}, {"Retry-After": "0"}),
            FakeResponse(429, {"error": "rate limited"}),
            FakeResponse(200, {"data": []}),
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=burst, retry_base_seconds=0)  # type: ignore[arg-type]

    assert await client.models() == []
    assert len(burst.requests) == 3

    limited = {"error": "rate limited"}
    down = FakeSession([FakeResponse(429, limited) for _ in range(RETRY_ATTEMPTS)])
    client = OpenRouterClient(MemorySecrets(), session=down, retry_base_seconds=0)  # type: ignore[arg-type]
    with pytest.raises(OpenRouterError, match="429"):
        await client.models()
    assert len(down.requests) == RETRY_ATTEMPTS


async def test_retry_after_is_capped_and_ignored_when_it_is_not_a_delay() -> None:
    """A provider that asks for an hour must not stall an observer for an hour."""
    client = OpenRouterClient(MemorySecrets())  # type: ignore[arg-type]

    assert client._retry_delay(0, 3600.0) == MAX_RETRY_SLEEP_SECONDS
    assert client._retry_delay(0, 0.0) == 0.0
    http_date = FakeResponse(429, {}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    assert _retry_after(http_date) is None
    assert _retry_after(FakeResponse(429, {})) is None
    assert _retry_after(FakeResponse(429, {}, {"Retry-After": "-5"})) is None


async def test_openrouter_completion_requires_exact_model_and_strict_schema() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "id": "generation-1",
                    "model": "vendor/exact",
                    "provider": "Provider A",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"summary":"done"}'},
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.01},
                },
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }

    result = await client.complete_json(
        model="vendor/exact",
        messages=[{"role": "user", "content": "bounded transcript"}],
        schema_name="summary_v1",
        schema=schema,
        max_tokens=64,
        reasoning_enabled=False,
    )

    body = session.requests[0][2]["json"]
    assert body["model"] == "vendor/exact"
    assert body["stream"] is False
    assert body["reasoning"] == {"effort": "none"}
    assert body["max_completion_tokens"] == 64
    assert "max_tokens" not in body
    assert "temperature" not in body
    # `require_parameters` is the guarantee (a provider that honours the schema);
    # pinning to one provider on top of it is not, and cost a whole day of titles.
    assert body["provider"] == {"require_parameters": True, "allow_fallbacks": True}
    assert body["response_format"]["json_schema"] == {
        "name": "summary_v1",
        "strict": True,
        "schema": schema,
    }
    assert result.value == {"summary": "done"}
    assert result.input_tokens == 10
    assert result.output_tokens == 3
    assert result.provider_name == "Provider A"
    assert result.finish_reason == "stop"
    assert result.response_content_type == "string"
    assert result.response_content_length == 18


def _completion_payload(model: str = "vendor/exact") -> dict[str, Any]:
    return {
        "id": "generation-profile",
        "model": model,
        "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        "usage": {},
    }


async def test_openrouter_uses_the_models_advertised_bounded_profile() -> None:
    session = FakeSession([FakeResponse(200, _completion_payload("openai/gpt-5.6-luna"))])
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    client.set_model_catalog(
        [
            {
                "id": "openai/gpt-5.6-luna",
                "supported_parameters": [
                    "max_tokens",
                    "reasoning",
                    "reasoning_effort",
                    "response_format",
                    "structured_outputs",
                ],
                "reasoning": {"supported_efforts": ["none", "low"], "mandatory": False},
            }
        ]
    )

    await client.complete_json(
        model="openai/gpt-5.6-luna",
        messages=[{"role": "user", "content": "title this"}],
        schema_name="title_v1",
        schema={"type": "object"},
        max_tokens=32,
        reasoning_enabled=False,
    )

    body = session.requests[0][2]["json"]
    assert body["max_tokens"] == 32
    assert "max_completion_tokens" not in body
    assert body["reasoning"] == {"effort": "none"}
    assert "temperature" not in body


@pytest.mark.parametrize(
    ("supported_parameters", "reasoning", "expected_reasoning"),
    [
        (["max_completion_tokens", "response_format"], None, None),
        (
            ["max_completion_tokens", "reasoning", "response_format"],
            {"supported_efforts": ["low", "medium"], "mandatory": True},
            None,
        ),
        (
            ["max_completion_tokens", "reasoning", "response_format"],
            {"supported_efforts": ["none", "low"], "mandatory": False},
            {"effort": "none"},
        ),
    ],
)
async def test_reasoning_controls_follow_model_capabilities(
    supported_parameters: list[str],
    reasoning: dict[str, Any] | None,
    expected_reasoning: dict[str, Any] | None,
) -> None:
    session = FakeSession([FakeResponse(200, _completion_payload())])
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    model: dict[str, Any] = {
        "id": "vendor/exact",
        "supported_parameters": supported_parameters,
    }
    if reasoning is not None:
        model["reasoning"] = reasoning
    client.set_model_catalog([model])

    await client.complete_json(
        model="vendor/exact",
        messages=[{"role": "user", "content": "title this"}],
        schema_name="title_v1",
        schema={"type": "object"},
        max_tokens=32,
        reasoning_enabled=False,
    )

    body = session.requests[0][2]["json"]
    assert body.get("reasoning") == expected_reasoning
    assert body["max_completion_tokens"] == 32


async def test_parameter_routing_404_falls_back_without_weakening_contract() -> None:
    incompatible = {
        "error": {"message": "No endpoints found that can handle the requested parameters."}
    }
    session = FakeSession(
        [FakeResponse(404, incompatible), FakeResponse(200, _completion_payload())]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    client.set_model_catalog(
        [
            {
                "id": "vendor/exact",
                "supported_parameters": [
                    "max_completion_tokens",
                    "reasoning",
                    "response_format",
                ],
                "reasoning": {"supported_efforts": ["none", "low"]},
            }
        ]
    )

    await client.complete_json(
        model="vendor/exact",
        messages=[{"role": "user", "content": "title this"}],
        schema_name="title_v1",
        schema={"type": "object"},
        max_tokens=32,
        reasoning_enabled=False,
    )

    first = session.requests[0][2]["json"]
    second = session.requests[1][2]["json"]
    assert first["reasoning"] == {"effort": "none"}
    assert "reasoning" not in second
    assert first["max_completion_tokens"] == second["max_completion_tokens"] == 32
    assert first["response_format"] == second["response_format"]
    assert first["model"] == second["model"] == "vendor/exact"


async def test_unknown_model_falls_back_between_bounded_token_fields() -> None:
    incompatible = {
        "error": {"message": "No endpoints found that can handle the requested parameters."}
    }
    session = FakeSession(
        [FakeResponse(404, incompatible), FakeResponse(200, _completion_payload())]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]

    await client.complete_json(
        model="vendor/exact",
        messages=[{"role": "user", "content": "title this"}],
        schema_name="title_v1",
        schema={"type": "object"},
        max_tokens=32,
    )

    first = session.requests[0][2]["json"]
    second = session.requests[1][2]["json"]
    assert first["max_completion_tokens"] == 32
    assert second["max_tokens"] == 32
    assert "max_tokens" not in first
    assert "max_completion_tokens" not in second


async def test_non_parameter_404_does_not_change_the_request_profile() -> None:
    missing = {"error": {"message": "Model vendor/missing was not found"}}
    session = FakeSession([FakeResponse(404, missing)])
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError, match="vendor/missing") as captured:
        await client.complete_json(
            model="vendor/missing",
            messages=[{"role": "user", "content": "title this"}],
            schema_name="title_v1",
            schema={"type": "object"},
            max_tokens=32,
        )

    assert captured.value.retryable is False
    assert len(session.requests) == 1


async def test_temporarily_unavailable_model_404_is_retryable() -> None:
    unavailable = {
        "error": {"message": "No allowed providers are available for the selected model"}
    }
    session = FakeSession([FakeResponse(404, unavailable)])
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError) as captured:
        await client.complete_json(
            model="vendor/exact",
            messages=[{"role": "user", "content": "title this"}],
            schema_name="title_v1",
            schema={"type": "object"},
            max_tokens=32,
        )

    assert "No allowed providers" in str(captured.value)
    assert captured.value.retryable is True


@pytest.mark.parametrize("content", [None, [], "not json"])
async def test_malformed_structured_response_is_retryable_and_keeps_safe_diagnostics(
    content: object,
) -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "id": "generation-bad",
                    "model": "vendor/exact",
                    "provider": "Provider B",
                    "choices": [
                        {"finish_reason": "length", "message": {"content": content}}
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 64, "cost": 0.02},
                },
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError) as captured:
        await client.complete_json(
            model="vendor/exact",
            messages=[{"role": "user", "content": "bounded transcript"}],
            schema_name="summary_v1",
            schema={"type": "object"},
            max_tokens=64,
        )

    error = captured.value
    assert error.retryable is True
    assert error.status == 200
    assert error.generation_id == "generation-bad"
    assert error.resolved_model == "vendor/exact"
    assert error.provider_name == "Provider B"
    assert error.finish_reason == "length"
    assert error.input_tokens == 12
    assert error.output_tokens == 64
    assert error.cost_usd == 0.02
    assert error.response_content_type in {"null", "array", "string"}


async def test_rate_limit_names_the_upstream_provider_and_marks_itself_retryable() -> None:
    """The one diagnostic that mattered used to be thrown away.

    Every title on 2026-07-31 failed with a bare "HTTP 429", which reads as an
    account problem. The body said otherwise — one upstream host was rate-limiting
    a model five other hosts were serving — and finding that out took a manual
    replay of the exact request. `retryable` is separate: it is what stops a
    rejected key being retried on the same curve as a busy provider.
    """
    body = {
        "error": {
            "message": "Provider returned error",
            "code": 429,
            "metadata": {
                "raw": "vendor/cheap is temporarily rate-limited upstream",
                "provider_name": "DeepInfra",
            },
        }
    }
    session = FakeSession([FakeResponse(429, body) for _ in range(RETRY_ATTEMPTS)])
    client = OpenRouterClient(MemorySecrets(), session=session, retry_base_seconds=0)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError) as captured:
        await client.test_key()

    assert "rate-limited upstream" in str(captured.value)
    assert "DeepInfra" in str(captured.value)
    assert captured.value.retryable is True


async def test_an_unusable_key_is_not_marked_retryable() -> None:
    """A refused key fails identically forever; retrying it only spends the budget."""
    session = FakeSession([FakeResponse(401, {"error": {"message": "No auth credentials"}})])
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError) as captured:
        await client.test_key()

    assert captured.value.retryable is False
    assert captured.value.status == 401

    missing = OpenRouterClient(MemorySecrets(""))  # type: ignore[arg-type]
    with pytest.raises(OpenRouterError) as unconfigured:
        await missing.test_key()
    assert unconfigured.value.retryable is False


async def test_a_key_quoted_back_in_a_provider_error_is_scrubbed() -> None:
    """Provider text reaches the firings table, so it is treated as publishable."""
    # Assembled rather than written out: a literal would trip the repo's own
    # pre-commit credential scanner, which is the same instinct being tested here.
    key_shaped = "-".join(["sk", "or", "v1", "abcdef0123456789"])
    body = {
        "error": {
            "message": "rejected",
            "metadata": {"raw": f"upstream rejected {key_shaped} for tenant"},
        }
    }
    session = FakeSession([FakeResponse(503, body) for _ in range(RETRY_ATTEMPTS)])
    client = OpenRouterClient(MemorySecrets(), session=session, retry_base_seconds=0)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError) as captured:
        await client.test_key()

    assert key_shaped not in str(captured.value)
    assert "[redacted]" in str(captured.value)


async def test_openrouter_rejects_oversized_and_redacts_http_error_body() -> None:
    oversized = FakeSession([FakeResponse(200, b"x" * (MAX_RESPONSE_BYTES + 1))])
    client = OpenRouterClient(MemorySecrets(), session=oversized)  # type: ignore[arg-type]
    with pytest.raises(OpenRouterError, match="size limit"):
        await client.test_key()

    failed = FakeSession([FakeResponse(401, {"error": "secret provider detail"})])
    client = OpenRouterClient(MemorySecrets(), session=failed)  # type: ignore[arg-type]
    with pytest.raises(OpenRouterError) as captured:
        await client.test_key()
    assert "secret provider detail" not in str(captured.value)
    assert "401" in str(captured.value)


async def test_a_rejected_request_carries_the_reason_it_was_rejected() -> None:
    """A 400 is the provider naming the part of our request it would not accept.

    Without the body every malformed schema, unsupported parameter, and over-long
    prompt persists as the same string, "request failed with HTTP 400", which
    cannot be acted on: `builtin:adaptive-title` failed every call it ever made on
    a `strict` schema missing one key from `required`, and the ledger row could not
    say so. A 400 is a request error, not an auth error, so its body is safe to
    keep - and key-shaped text is scrubbed from it regardless.
    """
    body = {
        "error": {
            "message": "Provider returned error",
            "metadata": {
                "raw": "Invalid schema for response_format: Missing 'confidence'.",
                "provider_name": "Azure",
            },
        }
    }
    session = FakeSession([FakeResponse(400, body)])
    client = OpenRouterClient(MemorySecrets(), session=session, retry_base_seconds=0)  # type: ignore[arg-type]

    with pytest.raises(OpenRouterError) as captured:
        await client.test_key()

    assert "Missing 'confidence'" in str(captured.value)
    assert "Azure" in str(captured.value)
    assert captured.value.status == 400
    # A malformed request fails identically forever; retrying only spends budget.
    assert captured.value.retryable is False


async def test_model_refresh_error_preserves_last_success_timestamp(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await store.cache_models([{"id": "vendor/model"}])
    before = await store.model_cache()
    await store.record_model_error("temporary failure")
    after = await store.model_cache()
    store.close()

    assert after["models"] == before["models"]
    assert after["fetched_at"] == before["fetched_at"]
    assert after["error"] == "temporary failure"


async def test_model_cache_serves_the_catalog_alphabetically(tmp_path: Path) -> None:
    # OpenRouter serves `/models` in its own order, which the picker showed verbatim - and a
    # catalogue nobody can navigate is the reported defect. Sorting happens on the way *out*
    # of the cache, so an install that fetched its catalog before this existed is fixed too,
    # without anyone pressing Refresh.
    store = AutomationStore(tmp_path / "mux.db")
    await store.cache_models(
        [
            {"id": "z/zeta", "name": "Zeta"},
            {"id": "b/beta", "name": "beta"},
            {"id": "a/alpha", "name": "Alpha"},
        ]
    )
    cached = await store.model_cache()
    store.close()

    # Case-folded: "beta" must not sort after "Zeta" because of its lowercase b.
    assert [model["id"] for model in cached["models"]] == ["a/alpha", "b/beta", "z/zeta"]


async def test_model_cache_breaks_a_duplicate_name_tie_by_id(tmp_path: Path) -> None:
    # Two providers shipping one display name is routine, and a tie broken by insertion order
    # is not a stable order to a reader scanning the list twice.
    store = AutomationStore(tmp_path / "mux.db")
    await store.cache_models(
        [
            {"id": "vendor-b/sonnet", "name": "Sonnet"},
            {"id": "vendor-a/sonnet", "name": "Sonnet"},
            {"id": "vendor-c/nameless"},
        ]
    )
    cached = await store.model_cache()
    store.close()

    assert [model["id"] for model in cached["models"]] == [
        "vendor-a/sonnet",
        "vendor-b/sonnet",
        # An entry with no name sorts under its id, which is also what the picker shows for it.
        "vendor-c/nameless",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI proving-platform contract")
def test_dpapi_persistence_round_trip_is_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    path = tmp_path / "automation.secrets.json"
    store = PlatformSecretStore(path)

    store.set("openrouter_api_key", "sk-or-plaintext")

    assert store.get("openrouter_api_key") == "sk-or-plaintext"
    assert b"sk-or-plaintext" not in path.read_bytes()
    assert store.status("openrouter_api_key") == {
        "configured": True,
        "source": "stored",
        "persistent": True,
        # DPAPI is a real encryption backend, so both halves are true here. They
        # come apart only on the opt-in POSIX file fallback, which is what
        # `encrypted` exists to distinguish.
        "encrypted": True,
        "backend": "dpapi",
    }
    store.clear("openrouter_api_key")
    assert store.get("openrouter_api_key") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI proving-platform contract")
async def test_failed_replace_preserves_working_key_and_never_echoes_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = PlatformSecretStore(tmp_path / "automation.secrets.json")
    store.set("openrouter_api_key", "working-key")
    app = web.Application()
    app["config"] = Config(data_dir=tmp_path)
    app["secret_store"] = store
    app["openrouter"] = RejectingProvider()
    app["automation_store"] = VerificationStoreStub()
    app.router.add_post("/key", automation_provider_key)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/key",
            json={"operation": "replace", "key": "bad-replacement", "test": True},
        )
        payload = await response.json()

    assert response.status == 422
    assert store.get("openrouter_api_key") == "working-key"
    assert "working-key" not in json.dumps(payload)
    assert "bad-replacement" not in json.dumps(payload)


# --------------------------------------------------------------------------- #
# Streamed tool completions
# --------------------------------------------------------------------------- #


def sse(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def content_chunk(text: str) -> dict[str, Any]:
    return {"id": "gen-1", "model": "vendor/tool", "choices": [{"delta": {"content": text}}]}


async def collect(client: OpenRouterClient, deltas: list[str]) -> Any:
    async def on_content(delta: str) -> None:
        deltas.append(delta)

    return await client.complete_tools(
        model="vendor/tool",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=256,
        on_content=on_content,
    )


async def test_a_streamed_completion_reassembles_the_ordinary_envelope() -> None:
    # Usage rides only the final chunk (`stream_options.include_usage`), and it
    # is what the spend ledger bills against: dropping it would silently record
    # every streamed assistant turn as free.
    session = FakeSession(
        [
            FakeResponse(
                200,
                sse(
                    content_chunk("Three sessions "),
                    content_chunk("are working."),
                    {
                        "id": "gen-1",
                        "model": "vendor/tool-resolved",
                        "provider": "acme",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 40, "completion_tokens": 9, "cost": 0.004},
                    },
                ),
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    deltas: list[str] = []
    turn = await collect(client, deltas)

    assert deltas == ["Three sessions ", "are working."]
    assert turn.content == "Three sessions are working."
    assert turn.resolved_model == "vendor/tool-resolved"
    assert turn.finish_reason == "stop"
    assert (turn.input_tokens, turn.output_tokens, turn.cost_usd) == (40, 9, 0.004)
    assert session.requests[0][2]["json"]["stream"] is True
    assert session.requests[0][2]["json"]["stream_options"] == {"include_usage": True}


async def test_streamed_tool_calls_are_merged_by_index_not_appended() -> None:
    # A tool call arrives as fragments: the name in one chunk and its JSON
    # arguments spread over many. Appending them as separate calls would hand
    # the assistant a call with no name and another with unparsable arguments.
    session = FakeSession(
        [
            FakeResponse(
                200,
                sse(
                    {
                        "choices": [{"delta": {"tool_calls": [
                            {"index": 0, "id": "call-1", "type": "function",
                             "function": {"name": "write_project_note", "arguments": ""}}
                        ]}}]
                    },
                    {
                        "choices": [{"delta": {"tool_calls": [
                            {"index": 0, "function": {"arguments": '{"project": "pix'}}
                        ]}}]
                    },
                    {
                        "choices": [{"delta": {"tool_calls": [
                            {"index": 0, "function": {"arguments": 'el lab"}'}}
                        ]}}]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                ),
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    turn = await collect(client, [])

    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call["id"] == "call-1"
    assert call["function"]["name"] == "write_project_note"
    assert json.loads(call["function"]["arguments"]) == {"project": "pixel lab"}
    assert turn.message["tool_calls"] == turn.tool_calls


async def test_a_provider_that_will_not_stream_answers_unstreamed() -> None:
    # Streaming is a latency optimization, never a capability the reply depends
    # on, so a provider that rejects the streaming parameters still answers.
    session = FakeSession(
        [
            FakeResponse(400, {"error": {"message": "stream is not supported"}}),
            FakeResponse(
                200,
                {
                    "id": "gen-2",
                    "model": "vendor/tool",
                    "choices": [
                        {"message": {"role": "assistant", "content": "Buffered."},
                         "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    deltas: list[str] = []
    turn = await collect(client, deltas)

    assert turn.content == "Buffered."
    assert deltas == [], "nothing was streamed, so nothing may have been spoken"
    assert session.requests[1][2]["json"]["stream"] is False


async def test_an_empty_stream_falls_back_rather_than_failing_the_turn() -> None:
    session = FakeSession(
        [
            FakeResponse(200, b": keep-alive\n\ndata: [DONE]\n\n"),
            FakeResponse(
                200,
                {
                    "id": "gen-3",
                    "model": "vendor/tool",
                    "choices": [{"message": {"role": "assistant", "content": "Recovered."}}],
                    "usage": {},
                },
            ),
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    assert (await collect(client, [])).content == "Recovered."


async def test_a_stream_that_breaks_after_speaking_is_not_retried() -> None:
    # Once text has been handed to the caller it has been spoken aloud. Retrying
    # would say it a second time, so a mid-reply break is a hard failure.
    class BreakingContent:
        async def iter_chunked(self, _size: int):  # type: ignore[no-untyped-def]
            yield b"data: " + json.dumps(content_chunk("Half a sen")).encode() + b"\n\n"
            raise TimeoutError

    broken = FakeResponse(200, b"")
    broken.content = BreakingContent()  # type: ignore[assignment]
    session = FakeSession([broken])
    client = OpenRouterClient(MemorySecrets(), session=session, retry_base_seconds=0)  # type: ignore[arg-type]
    deltas: list[str] = []
    with pytest.raises(OpenRouterError, match="mid-reply"):
        await collect(client, deltas)
    assert deltas == ["Half a sen"]
    assert len(session.requests) == 1, "a spoken reply must never be re-requested"


async def test_an_unstreamed_completion_still_takes_the_buffered_path() -> None:
    # No callback means nothing is waiting on the first token, and a buffered
    # response is simpler to retry.
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "id": "gen-4",
                    "model": "vendor/tool",
                    "choices": [{"message": {"role": "assistant", "content": "Plain."}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1},
                },
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    turn = await client.complete_tools(
        model="vendor/tool",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=256,
    )
    assert turn.content == "Plain."
    assert session.requests[0][2]["json"]["stream"] is False
    assert "stream_options" not in session.requests[0][2]["json"]


# --------------------------------------------------------------------------- #
# Prompt caching (Phase 15)
# --------------------------------------------------------------------------- #


def test_every_openrouter_route_is_marked_and_only_a_custom_endpoint_is_not() -> None:
    # OpenRouter translates the marker into whatever the routed provider
    # understands, so a marked prompt is understood everywhere it routes: an
    # Anthropic-style block reaches a supporting OpenAI model as a
    # `prompt_cache_breakpoint`. Anthropic and Qwen are the ones that cache
    # *only* where a marker says to; the rest would find the prefix unaided and
    # lose nothing by being told where it ends.
    assert marks_cache_breakpoints("anthropic/claude-sonnet-4.5")
    assert marks_cache_breakpoints("anthropic/claude-opus-4.1:beta")
    assert marks_cache_breakpoints("openai/gpt-5.6-terra")
    assert marks_cache_breakpoints("deepseek/deepseek-chat")
    assert marks_cache_breakpoints("some-new-vendor/model")
    # A custom endpoint has no translation layer in front of it, and an empty id
    # is not a route at all.
    assert not marks_cache_breakpoints("anthropic/claude-sonnet-4.5", cache_policy="unknown")
    assert not marks_cache_breakpoints("")


def test_a_marked_message_becomes_one_text_part_carrying_the_breakpoint() -> None:
    marked = cache_stable_message(
        {"role": "system", "content": "primer"}, model="anthropic/claude-sonnet-4.5"
    )
    assert marked == {
        "role": "system",
        "content": [
            {"type": "text", "text": "primer", "cache_control": {"type": "ephemeral"}}
        ],
    }


def test_only_an_untranslated_endpoint_gets_its_plain_string_back() -> None:
    # The marked shape is sent wherever OpenRouter can translate it, which is
    # everywhere it routes. A custom endpoint is the one place it cannot.
    original = {"role": "system", "content": "primer"}
    assert cache_stable_message(
        original, model="openai/gpt-5.6-terra", cache_policy="unknown"
    ) is original
    marked = cache_stable_message(original, model="openai/gpt-5.6-terra")
    assert marked is not original
    assert marked["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_marking_never_mutates_the_message_it_was_handed() -> None:
    original = {"role": "system", "content": "primer"}
    cache_stable_message(original, model="anthropic/claude-sonnet-4.5")
    assert original == {"role": "system", "content": "primer"}


def test_marking_an_already_parted_message_moves_the_breakpoint_to_the_end() -> None:
    marked = cache_stable_message(
        {"role": "system", "content": [{"type": "text", "text": "a"},
                                       {"type": "text", "text": "b"}]},
        model="anthropic/claude-sonnet-4.5",
    )
    parts = marked["content"]
    assert "cache_control" not in parts[0]
    assert parts[1]["cache_control"] == {"type": "ephemeral"}


def test_a_message_with_no_markable_content_is_left_exactly_as_it_was() -> None:
    for message in (
        {"role": "system", "content": ""},
        {"role": "system", "content": []},
        {"role": "assistant", "tool_calls": []},
    ):
        assert cache_stable_message(message, model="anthropic/claude-sonnet-4.5") is message


def test_cached_prompt_tokens_reads_either_shape_a_provider_reports() -> None:
    assert cached_prompt_tokens({"prompt_tokens_details": {"cached_tokens": 1800}}) == 1800
    assert cached_prompt_tokens({"cached_tokens": 900}) == 900
    # The nested reading wins when both are present: it is the normalised one.
    assert cached_prompt_tokens(
        {"cached_tokens": 5, "prompt_tokens_details": {"cached_tokens": 1800}}
    ) == 1800
    # Absent, malformed, and negative all read as "no measured hit" rather than
    # raising into a call that otherwise succeeded.
    assert cached_prompt_tokens({"prompt_tokens": 400}) == 0
    assert cached_prompt_tokens({"prompt_tokens_details": {"cached_tokens": "n/a"}}) == 0
    assert cached_prompt_tokens({"cached_tokens": -3}) == 0
    assert cached_prompt_tokens(None) == 0


async def test_an_unstreamed_tool_turn_carries_its_cached_prompt_tokens() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "id": "gen-5",
                    "model": "anthropic/claude-sonnet-4.5",
                    "choices": [{"message": {"role": "assistant", "content": "Cached."}}],
                    "usage": {
                        "prompt_tokens": 2400,
                        "completion_tokens": 12,
                        "prompt_tokens_details": {"cached_tokens": 2000},
                    },
                },
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    turn = await client.complete_tools(
        model="anthropic/claude-sonnet-4.5",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=256,
    )
    # A subset of the prompt tokens, never added to them.
    assert turn.input_tokens == 2400
    assert turn.cached_tokens == 2000


async def test_a_streamed_tool_turn_carries_its_cached_prompt_tokens() -> None:
    # Usage rides only the final chunk, so the cached figure is lost with it if
    # the accumulator drops anything but the two headline counters.
    session = FakeSession(
        [
            FakeResponse(
                200,
                sse(
                    content_chunk("Cached."),
                    {
                        "id": "gen-6",
                        "model": "anthropic/claude-sonnet-4.5",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {
                            "prompt_tokens": 2400,
                            "completion_tokens": 12,
                            "prompt_tokens_details": {"cached_tokens": 2000},
                        },
                    },
                ),
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    deltas: list[str] = []
    turn = await collect(client, deltas)
    assert turn.cached_tokens == 2000


def test_cache_write_tokens_read_either_shape_and_default_to_a_true_zero() -> None:
    # The counterpart to the hit count, and the one that separates "no caching"
    # from "wrote a cache nothing read" - which costs 25% more than no caching at
    # all on the providers that bill a write above input.
    assert cache_write_prompt_tokens(
        {"prompt_tokens_details": {"cache_write_tokens": 6400}}
    ) == 6400
    assert cache_write_prompt_tokens({"cache_write_tokens": 900}) == 900
    assert cache_write_prompt_tokens(
        {"cache_write_tokens": 5, "prompt_tokens_details": {"cache_write_tokens": 6400}}
    ) == 6400
    # Providers that write for free report nothing, and zero is then the true
    # reading rather than a missing one.
    assert cache_write_prompt_tokens({"prompt_tokens": 400}) == 0
    assert cache_write_prompt_tokens(None) == 0


def test_the_cache_discount_is_signed_and_absent_is_not_zero() -> None:
    assert cache_discount_usd({"cache_discount": 0.0042}) == pytest.approx(0.0042)
    # Negative is a write turn billed above input. It is the reading that says a
    # breakpoint sits over content changing every call, so it must survive.
    assert cache_discount_usd({"cache_discount": -0.0031}) == pytest.approx(-0.0031)
    # `None` says the provider made no measurement; zero would claim one.
    assert cache_discount_usd({"prompt_tokens": 10}) is None
    assert cache_discount_usd(None) is None


def test_sticky_routing_is_sent_to_openrouter_and_withheld_from_a_custom_endpoint() -> None:
    """A cache lives on the instance that wrote it, and OpenRouter balances across
    instances; the session key is what routes the next turn back to it.

    A custom OpenAI-compatible server has no routing layer to steer and is
    exactly the kind of thing that rejects an unknown field, so the graceful
    degradation is to omit it and lose nothing that was ever available.
    """
    body: dict[str, Any] = {"model": "openai/gpt-5.6-terra"}
    apply_session_routing(body, "dialog-1", endpoint=openrouter_endpoint())
    assert body["session_id"] == "dialog-1"
    # Full usage accounting rides along: cost is reported without it, the cache
    # figures are not.
    assert body["usage"] == {"include": True}

    custom: dict[str, Any] = {"model": "qwen2.5-coder:7b"}
    apply_session_routing(
        custom,
        "dialog-1",
        endpoint=custom_endpoint(base_url="http://localhost:11434/v1", model="qwen2.5-coder:7b"),
    )
    assert "session_id" not in custom
    assert "usage" not in custom


def test_a_session_key_is_clipped_to_the_documented_ceiling() -> None:
    body: dict[str, Any] = {}
    apply_session_routing(body, "x" * 400, endpoint=openrouter_endpoint())
    assert body["session_id"] == "x" * MAX_SESSION_ID_CHARS
    # Nothing to pin means nothing sent, rather than an empty key that groups
    # every conversation into one.
    blank: dict[str, Any] = {}
    apply_session_routing(blank, "   ", endpoint=openrouter_endpoint())
    assert "session_id" not in blank


async def test_a_tool_turn_carries_its_cache_write_and_discount() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "id": "gen-7",
                    "model": "openai/gpt-5.6-terra",
                    "choices": [{"message": {"role": "assistant", "content": "Wrote."}}],
                    "usage": {
                        "prompt_tokens": 8100,
                        "completion_tokens": 19,
                        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 8100},
                        "cache_discount": -0.004,
                    },
                },
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    turn = await client.complete_tools(
        model="openai/gpt-5.6-terra",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=256,
        session_id="dialog-1",
    )
    # The exact shape the ledger showed before sticky routing: everything
    # written, nothing read, and a negative discount pricing the premium.
    assert turn.cached_tokens == 0
    assert turn.cache_write_tokens == 8100
    assert turn.cache_discount_usd == pytest.approx(-0.004)
    assert session.requests[-1][2]["json"]["session_id"] == "dialog-1"


async def test_the_catalog_carries_each_model_s_caching_economics() -> None:
    """Read from pricing rather than from a list of providers who cache.

    A provider list goes stale the week a new one appears; the pricing does not,
    and it answers the question a model chooser actually has before switching:
    does this model cache at all, and does a write cost extra.
    """
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "openai/gpt-5.6-terra",
                            "supported_parameters": ["response_format"],
                            "architecture": {"modality": "text->text"},
                            "pricing": {
                                "prompt": "0.000002",
                                "completion": "0.000012",
                                "input_cache_read": "0.0000002",
                                "input_cache_write": "0.0000025",
                            },
                        },
                        {
                            "id": "deepseek/deepseek-v4-pro",
                            "supported_parameters": ["response_format"],
                            "architecture": {"modality": "text->text"},
                            # Reads priced, writes free: caching is pure upside.
                            "pricing": {"prompt": "0.0000004", "input_cache_read": "0.000000033"},
                        },
                    ]
                },
            )
        ]
    )
    client = OpenRouterClient(MemorySecrets(), session=session)  # type: ignore[arg-type]
    catalog = {item["id"]: item for item in await client.models()}
    assert catalog["openai/gpt-5.6-terra"]["cache_read_price"] == pytest.approx(0.0000002)
    assert catalog["openai/gpt-5.6-terra"]["cache_write_price"] == pytest.approx(0.0000025)
    # Absent is not zero: a free write reports nothing, and so does a model that
    # does not cache, which the read price is what tells apart.
    assert catalog["deepseek/deepseek-v4-pro"]["cache_write_price"] is None
    assert catalog["deepseek/deepseek-v4-pro"]["cache_read_price"] == pytest.approx(0.000000033)


def test_the_cache_saving_is_priced_from_the_catalog_including_the_write_premium() -> None:
    """Derived because nothing reports it, and signed because a write can cost more.

    `cache_discount` lives in OpenRouter's `/generation` stats and not in a
    completion's usage payload, so the measured column is `None` on every call
    the assistant makes. The catalog's published prices are always there.
    """
    catalog = {
        "openai/gpt-5.6-terra": {
            "prompt_price": 0.000002, "cache_read_price": 0.0000002,
            "cache_write_price": 0.0000025,
        },
        "deepseek/deepseek-v4-pro": {
            "prompt_price": 0.0000004, "cache_read_price": 0.000000033,
        },
    }
    # 4,975 read at 0.1x saves 4,975 x 1.8e-6; 1,025 written at 1.25x costs
    # 1,025 x 0.5e-6 on top of ordinary input.
    saving, priced = cache_saving_usd(
        [{"model": "openai/gpt-5.6-terra", "cached_tokens": 4975, "cache_write_tokens": 1025}],
        catalog,
    )
    assert priced == 1
    assert saving == pytest.approx(4975 * 0.0000018 - 1025 * 0.0000005)

    # A cold prefix: everything written, nothing read. The premium is the whole
    # figure and it is negative, which is the reading that says the breakpoint
    # sits above content that changes every call.
    cold, _ = cache_saving_usd(
        [{"model": "openai/gpt-5.6-terra", "cached_tokens": 0, "cache_write_tokens": 8100}],
        catalog,
    )
    assert cold is not None and cold < 0

    # A provider that publishes no write price charges nothing for one, which is
    # a real reading rather than a missing one.
    free_writes, _ = cache_saving_usd(
        [{"model": "deepseek/deepseek-v4-pro", "cached_tokens": 1000, "cache_write_tokens": 9000}],
        catalog,
    )
    assert free_writes == pytest.approx(1000 * (0.0000004 - 0.000000033))


def test_an_unpriceable_model_contributes_nothing_rather_than_zero() -> None:
    # A total assembled from no prices is not a total, and a `0` would read as
    # "caching saved nothing" rather than "nobody could price this".
    catalog: dict[str, dict[str, object]] = {"vendor/known": {
        "prompt_price": 0.000001, "cache_read_price": 0.0000001,
    }}
    absent, priced = cache_saving_usd(
        [{"model": "vendor/unknown", "cached_tokens": 5000, "cache_write_tokens": 0}], catalog
    )
    assert absent is None and priced == 0
    # A model in the catalog with no cache pricing is equally unpriceable.
    uncacheable, _ = cache_saving_usd(
        [{"model": "vendor/plain", "cached_tokens": 5000, "cache_write_tokens": 0}],
        {"vendor/plain": {"prompt_price": 0.000001}},
    )
    assert uncacheable is None
    # Partial coverage still answers, and says how much of it was priced.
    mixed, count = cache_saving_usd(
        [
            {"model": "vendor/known", "cached_tokens": 1000, "cache_write_tokens": 0},
            {"model": "vendor/unknown", "cached_tokens": 9000, "cache_write_tokens": 0},
        ],
        catalog,
    )
    assert count == 1
    assert mixed == pytest.approx(1000 * 0.0000009)
