from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from .secret_store import SecretStore

OPENROUTER_ORIGIN = "https://openrouter.ai/api/v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}
# A rate limit is the common failure here, not a transient network blip: several
# panes each fire an observer at the same turn boundary, so 429 arrives in bursts.
# The old 0.25s/0.5s pair was shorter than the burst it was meant to ride out and
# effectively made 429 fatal — measured 20 of 70 titler calls lost in a day. Four
# retries with equal-jitter exponential backoff cover a multi-second burst while
# still bounding a single call: 0.5 + 1 + 2 + 4 is 7.5s of sleep worst case, well
# inside the caller's tolerance for a background observer.
RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 0.5
MAX_RETRY_SLEEP_SECONDS = 8.0
# How much of the provider's own error text to keep. A 429 from OpenRouter names the
# upstream provider and says whether the limit is the account's or that provider's —
# the difference between "add credit" and "wait", and unrecoverable once discarded.
MAX_PROVIDER_ERROR_CHARS = 400
# Anything key-shaped is scrubbed out of a message that will be persisted, whatever
# put it there. Covers OpenRouter's own `sk-or-v1-…` and the generic `sk-…` an
# upstream provider might quote back at us.
SECRET_SHAPED = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}", re.IGNORECASE)


class OpenRouterError(RuntimeError):
    """An OpenRouter call that failed, carrying whether trying again could help.

    Callers schedule background retries off `retryable`. Without it every failure
    looks alike, and a misconfigured key is retried on the same curve as a rate
    limit — spending the retry budget on the one thing that cannot come back.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


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
        retry_base_seconds: float = RETRY_BASE_SECONDS,
    ) -> None:
        self.secrets = secrets
        self.timeout_seconds = timeout_seconds
        self.retry_base_seconds = retry_base_seconds
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
        # Held across attempts so the final raise can report the provider's own words
        # rather than the bare status the last attempt happened to see.
        detail = ""
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        client = await self._client()
        last_attempt = RETRY_ATTEMPTS - 1
        for attempt in range(RETRY_ATTEMPTS):
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
                    retry_after = _retry_after(response)
                    if response.status in RETRY_STATUSES:
                        # Only ever read for statuses that mean "the far side is busy
                        # or broken". An auth failure's body is the one that can echo
                        # the credential that was rejected, and it stays redacted.
                        detail = _provider_error(body)
                    if response.status in RETRY_STATUSES and attempt < last_attempt:
                        await asyncio.sleep(self._retry_delay(attempt, retry_after))
                        continue
                    if response.status >= 400:
                        raise OpenRouterError(
                            f"OpenRouter request failed with HTTP {response.status}"
                            + (f": {detail}" if detail else ""),
                            status=response.status,
                            retryable=response.status in RETRY_STATUSES,
                            retry_after=retry_after,
                        )
                    try:
                        value = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise OpenRouterError("OpenRouter returned invalid JSON") from exc
                    if not isinstance(value, dict):
                        raise OpenRouterError("OpenRouter returned an invalid response envelope")
                    return value
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt < last_attempt:
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue
                raise OpenRouterError(
                    f"OpenRouter request failed: {type(exc).__name__}", retryable=True
                ) from exc
        # Only reachable when every attempt drew a retryable status, so the caller's
        # longer-horizon retry is exactly the right next move.
        raise OpenRouterError(
            "OpenRouter request failed" + (f": {detail}" if detail else ""), retryable=True
        )

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        """Equal-jitter exponential backoff, with the server's own hint preferred.

        Jitter matters more than the curve here: the calls that collide are the ones
        a shared turn boundary released together, so an unjittered backoff retries
        them together too and reproduces the burst that caused the 429.
        """
        if retry_after is not None:
            return min(retry_after, MAX_RETRY_SLEEP_SECONDS)
        ceiling = min(self.retry_base_seconds * (2**attempt), MAX_RETRY_SLEEP_SECONDS)
        return float(ceiling * (0.5 + random.random() * 0.5))

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
                # `require_parameters` is the guarantee that matters: it restricts
                # routing to providers that actually honour `response_format`, so a
                # fallback still returns the schema rather than prose. Pinning to the
                # single top-ranked provider on top of that bought nothing and made
                # one provider's bad hour a total outage — every title on
                # 2026-07-31 failed with "temporarily rate-limited upstream" from
                # DeepInfra while five other providers served the same model. There
                # is no fallback to a *different model* here, so the answer cannot
                # silently change quality; only which host produced it.
                "provider": {"require_parameters": True, "allow_fallbacks": True},
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


def _retry_after(response: Any) -> float | None:
    """Seconds from a ``Retry-After`` header, when it is a usable delay.

    Only the delta-seconds form is honoured. The HTTP-date form is legal but is
    never what a rate limiter sends here, and parsing it would mean trusting the
    provider's clock against ours.
    """
    raw = (getattr(response, "headers", None) or {}).get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds else None


def _provider_error(body: bytes | bytearray) -> str:
    """The provider's own explanation of a busy-or-broken call, when it sent one.

    A bare "HTTP 429" is the same string whether the account is out of credit, the
    key is throttled, or one upstream host is having a bad hour — three different
    fixes. OpenRouter puts the distinction in `error.metadata.raw` and names the
    host in `error.metadata.provider_name`; discarding it costs an hour of guessing
    later, so it is carried into the message.

    Read only for statuses that describe the far side's health, never for an auth
    failure, and key-shaped text is scrubbed regardless: this string ends up in the
    firings table and on the automation status surface.
    """
    try:
        payload = json.loads(bytes(body))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    metadata = error.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    parts = [str(metadata.get("raw") or error.get("message") or "").strip()]
    provider = str(metadata.get("provider_name") or "").strip()
    if provider:
        parts.append(f"(provider: {provider})")
    text = " ".join(part for part in parts if part)[:MAX_PROVIDER_ERROR_CHARS]
    return SECRET_SHAPED.sub("[redacted]", text)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
