from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.automation_store import AutomationStore
from swe_mux.openrouter import MAX_RESPONSE_BYTES, OpenRouterClient, OpenRouterError
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
    def __init__(self, status: int, value: Any) -> None:
        self.status = status
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
    async def test_key(self, _candidate: str | None = None) -> dict[str, Any]:
        raise OpenRouterError("OpenRouter request failed with HTTP 401")


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


async def test_openrouter_completion_requires_exact_model_and_strict_schema() -> None:
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "id": "generation-1",
                    "model": "vendor/exact",
                    "choices": [{"message": {"content": '{"summary":"done"}'}}],
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
    )

    body = session.requests[0][2]["json"]
    assert body["model"] == "vendor/exact"
    assert body["stream"] is False
    assert body["provider"] == {"require_parameters": True, "allow_fallbacks": False}
    assert body["response_format"]["json_schema"] == {
        "name": "summary_v1",
        "strict": True,
        "schema": schema,
    }
    assert result.value == {"summary": "done"}
    assert result.input_tokens == 10
    assert result.output_tokens == 3


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
    app["secret_store"] = store
    app["openrouter"] = RejectingProvider()
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
