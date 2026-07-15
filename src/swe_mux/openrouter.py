from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from .secret_store import SecretStore

OPENROUTER_ORIGIN = "https://openrouter.ai/api/v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class OpenRouterError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class OpenRouterResult:
    generation_id: str | None
    requested_model: str
    resolved_model: str
    value: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int


class OpenRouterClient:
    """Fixed-origin, bounded OpenRouter client. No caller-controlled URLs are accepted."""

    def __init__(
        self,
        secrets: SecretStore,
        *,
        timeout_seconds: float = 30,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.secrets = secrets
        self.timeout_seconds = timeout_seconds
        self._session = session
        self._owned_session = False

    async def close(self) -> None:
        if self._owned_session and self._session:
            await self._session.close()
        self._session = None
        self._owned_session = False

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owned_session = True
        return self._session

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        key: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = key or self.secrets.get("openrouter_api_key")
        if not token:
            raise OpenRouterError("OpenRouter key is not configured")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = await self._client()
        for attempt in range(3):
            try:
                async with client.request(
                    method,
                    f"{OPENROUTER_ORIGIN}{endpoint}",
                    headers=headers,
                    json=json_body,
                    params=params,
                    allow_redirects=False,
                ) as response:
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise OpenRouterError("OpenRouter response exceeded the size limit")
                    if response.status in {429, 500, 502, 503, 504} and attempt < 2:
                        await asyncio.sleep(0.25 * (2**attempt))
                        continue
                    if response.status >= 400:
                        raise OpenRouterError(
                            f"OpenRouter request failed with HTTP {response.status}"
                        )
                    try:
                        value = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise OpenRouterError("OpenRouter returned invalid JSON") from exc
                    if not isinstance(value, dict):
                        raise OpenRouterError("OpenRouter returned an invalid response envelope")
                    return value
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt < 2:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise OpenRouterError("OpenRouter request failed") from exc
        raise OpenRouterError("OpenRouter request failed")

    async def test_key(self, candidate: str | None = None) -> dict[str, Any]:
        payload = await self._request("GET", "/models", key=candidate)
        return {"ok": True, "models": len(payload.get("data") or [])}

    async def models(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/models")
        result: list[dict[str, Any]] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            supported = set(item.get("supported_parameters") or [])
            architecture = item.get("architecture") or {}
            modality = str(architecture.get("modality") or "")
            if "response_format" not in supported and "structured_outputs" not in supported:
                continue
            output_modalities = modality.rsplit("->", 1)[-1].split("+") if modality else []
            if output_modalities and "text" not in output_modalities:
                continue
            pricing = item.get("pricing") or {}
            result.append(
                {
                    "id": str(item["id"]),
                    "name": str(item.get("name") or item["id"]),
                    "context_length": int(item.get("context_length") or 0),
                    "prompt_price": _number(pricing.get("prompt")),
                    "completion_price": _number(pricing.get("completion")),
                    "supported_parameters": sorted(supported),
                }
            )
        return result

    async def complete_json(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> OpenRouterResult:
        if not model:
            raise OpenRouterError("an exact OpenRouter model id is required")
        started = time.monotonic()
        payload = await self._request(
            "POST",
            "/chat/completions",
            json_body={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0,
                "max_tokens": max_tokens,
                "provider": {"require_parameters": True, "allow_fallbacks": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                },
            },
        )
        try:
            choice = payload["choices"][0]["message"]["content"]
            value = json.loads(choice) if isinstance(choice, str) else choice
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise OpenRouterError("OpenRouter structured response was missing or invalid") from exc
        if not isinstance(value, dict):
            raise OpenRouterError("OpenRouter structured response must be an object")
        usage = payload.get("usage") or {}
        return OpenRouterResult(
            generation_id=str(payload.get("id")) if payload.get("id") else None,
            requested_model=model,
            resolved_model=str(payload.get("model") or model),
            value=value,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=_number(usage.get("cost")),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def generation_cost(self, generation_id: str) -> float | None:
        payload = await self._request("GET", "/generation", params={"id": generation_id})
        data = payload.get("data") or {}
        return _number(data.get("total_cost") or data.get("usage"))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
