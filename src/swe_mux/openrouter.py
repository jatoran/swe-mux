from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

import aiohttp

from .llm_endpoint import (
    OPENROUTER_ORIGIN,
    CachePolicy,
    LlmEndpoint,
    openrouter_endpoint,
)
from .secret_store import SecretStore
from .text_safety import utf8_safe_value

__all__ = [
    "OPENROUTER_ORIGIN",
    "OpenRouterClient",
    "OpenRouterError",
    "OpenRouterResult",
    "OpenRouterToolTurn",
    "OpenRouterVerification",
    "cache_stable_message",
    "cached_prompt_tokens",
    "needs_explicit_cache_control",
]

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
SAFE_ERROR_DETAIL_STATUSES = RETRY_STATUSES | {404, 412, 413, 422}
# A provider that understood the request and rejected its streaming parameters
# can still answer unstreamed, so these statuses fall back rather than fail.
STREAM_UNSUPPORTED_STATUSES = {400, 422}
TOKEN_LIMIT_PARAMETERS = ("max_completion_tokens", "max_tokens")
PARAMETER_COMPATIBILITY_ERRORS = (
    "no endpoints found that can handle the requested parameters",
    "no endpoints found that support the requested parameters",
)
TRANSIENT_MODEL_AVAILABILITY_ERRORS = (
    "no allowed providers are available for the selected model",
    "no providers are available for the selected model",
)
# How much of the provider's own error text to keep. A 429 from OpenRouter names the
# upstream provider and says whether the limit is the account's or that provider's —
# the difference between "add credit" and "wait", and unrecoverable once discarded.
MAX_PROVIDER_ERROR_CHARS = 400
# Anything key-shaped is scrubbed out of a message that will be persisted, whatever
# put it there. Covers OpenRouter's own `sk-or-v1-…` and the generic `sk-…` an
# upstream provider might quote back at us.
SECRET_SHAPED = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}", re.IGNORECASE)
# Routing prefixes whose providers cache a repeated prompt prefix only when the
# request marks one. Everything absent from this set caches implicitly, so an
# unknown provider is treated as "send the prompt as it is" rather than as
# "send a marker it may reject" — the safe direction is a missed hit, never a
# rejected request.
EXPLICIT_CACHE_CONTROL_PROVIDERS = frozenset({"anthropic"})
# How much of a verification reply is kept. Enough to read a sentence and spot a
# chat template leaking its scaffolding; not enough to become a place model output
# accumulates in the database.
VERIFY_SAMPLE_CHARS = 400
log = logging.getLogger(__name__)


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
        generation_id: str | None = None,
        resolved_model: str | None = None,
        provider_name: str | None = None,
        finish_reason: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        response_content_type: str | None = None,
        response_content_length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.generation_id = generation_id
        self.resolved_model = resolved_model
        self.provider_name = provider_name
        self.finish_reason = finish_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.response_content_type = response_content_type
        self.response_content_length = response_content_length


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
    provider_name: str | None = None
    finish_reason: str | None = None
    response_content_type: str | None = None
    response_content_length: int | None = None
    cached_tokens: int = 0


@dataclass(slots=True, frozen=True)
class OpenRouterToolTurn:
    """One assistant turn from a tool-calling completion.

    `message` is the provider's assistant message verbatim (content plus any
    `tool_calls`), suitable for appending straight back onto the conversation
    the agentic loop is building.
    """

    generation_id: str | None
    requested_model: str
    resolved_model: str
    content: str
    tool_calls: list[dict[str, Any]]
    message: dict[str, Any]
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_ms: int
    provider_name: str | None = None
    #: Prompt tokens the provider served from its cache, out of `input_tokens`.
    #: Zero is ambiguous by construction - it means "no hit" and "this provider
    #: does not report caching" alike - so it is recorded, never asserted from.
    cached_tokens: int = 0


class _StreamUnsupported(RuntimeError):
    """Streaming failed in a way the unstreamed request can still answer."""


@dataclass
class _ToolStreamAccumulator:
    """Rebuilds one chat-completion envelope from its SSE deltas.

    Two things make this more than string concatenation. Tool calls arrive as
    fragments keyed by an `index` — the name in one chunk, the JSON arguments
    across many — so they are merged per index rather than appended, and a
    fragment with no index belongs to the call already open at position zero.
    And `usage` arrives only in the final chunk (`stream_options.include_usage`),
    which is what the spend ledger bills against: dropping it would silently
    record every streamed assistant turn as free.
    """

    content: str = ""
    saw_data: bool = False
    finish_reason: str | None = None
    generation_id: str | None = None
    resolved_model: str | None = None
    provider_name: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    calls: dict[int, dict[str, Any]] = field(default_factory=dict)

    def feed(self, raw: bytes) -> str:
        """Consume one SSE line; returns the text delta it carried, if any."""
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            return ""
        if not line.startswith("data:"):
            return ""
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return ""
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return ""  # a keep-alive or a partial frame; the next line resyncs
        if not isinstance(chunk, dict):
            return ""
        self.saw_data = True
        if chunk.get("id"):
            self.generation_id = str(chunk["id"])
        if chunk.get("model"):
            self.resolved_model = str(chunk["model"])
        if chunk.get("provider"):
            self.provider_name = str(chunk["provider"])
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        choice = choices[0]
        if not isinstance(choice, dict):
            return ""
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return ""
        self._merge_tool_calls(delta.get("tool_calls"))
        piece = delta.get("content")
        if not isinstance(piece, str) or not piece:
            return ""
        self.content += piece
        return piece

    def _merge_tool_calls(self, fragments: Any) -> None:
        if not isinstance(fragments, list):
            return
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            try:
                index = int(fragment.get("index", 0))
            except (TypeError, ValueError):
                index = 0
            call = self.calls.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if fragment.get("id"):
                call["id"] = str(fragment["id"])
            if fragment.get("type"):
                call["type"] = str(fragment["type"])
            function = fragment.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name"):
                call["function"]["name"] = str(function["name"])
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                call["function"]["arguments"] += arguments

    def envelope(self, requested_model: str) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.calls:
            message["tool_calls"] = [self.calls[index] for index in sorted(self.calls)]
        return {
            "id": self.generation_id,
            "model": self.resolved_model or requested_model,
            "provider": self.provider_name,
            "usage": self.usage,
            "choices": [{"message": message, "finish_reason": self.finish_reason}],
        }


@dataclass(slots=True, frozen=True)
class OpenRouterVerification:
    """One proving completion against a configured endpoint.

    `output` is the model's own words, kept because the whole point of a verify
    action is that a person reads what came back: an endpoint that answers with
    an empty string, a refusal, or a chat template's raw scaffolding is
    *reachable* and still not usable, and only the text distinguishes those from
    a good reply.
    """

    provider: str
    origin: str
    requested_model: str
    resolved_model: str
    output: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


@dataclass(slots=True, frozen=True)
class _ModelCapabilities:
    supported_parameters: frozenset[str]
    reasoning_efforts: frozenset[str] | None
    reasoning_mandatory: bool


class OpenRouterClient:
    """Bounded chat-completions client for one configured endpoint.

    The origin is **install configuration**, never a caller parameter. It was a
    module constant until a bring-your-own endpoint needed to exist; the
    substitution that replaced it is `endpoint`, resolved from `Config` by
    `llm_endpoint.resolve_endpoint`, and no method here accepts a URL. An agent,
    an MCP tool, or an HTTP body still cannot reach a destination the operator
    did not type into Settings, which is the property the old constant was
    protecting and the reason arbitrary network destinations remain on the
    decision-gated list.

    `endpoint` may be a callable so the answer is re-read per request: switching
    provider or fixing a typo'd base URL then takes effect on the next call
    rather than on the next daemon restart, which matters because the call that
    proves the fix is the verify action itself.

    The class keeps its name. It is the seam every model-backed feature already
    imports, and renaming it would have touched nine modules to say the same
    thing this docstring says.
    """

    def __init__(
        self,
        secrets: SecretStore,
        *,
        timeout_seconds: float = 30,
        session: aiohttp.ClientSession | None = None,
        retry_base_seconds: float = RETRY_BASE_SECONDS,
        endpoint: LlmEndpoint | Callable[[], LlmEndpoint] | None = None,
    ) -> None:
        self.secrets = secrets
        self.timeout_seconds = timeout_seconds
        self.retry_base_seconds = retry_base_seconds
        self._session = session
        self._owned_session = False
        self._model_capabilities: dict[str, _ModelCapabilities] = {}
        self._endpoint = endpoint

    @property
    def endpoint(self) -> LlmEndpoint:
        """The endpoint this call goes to, re-resolved every time it is asked."""
        if self._endpoint is None:
            return openrouter_endpoint()
        if callable(self._endpoint):
            return self._endpoint()
        return self._endpoint

    def _auth_headers(self, endpoint: LlmEndpoint, key: str | None) -> dict[str, str]:
        """Headers for one request, refusing only where a key is actually required.

        A custom endpoint with no key is the normal case, not a misconfiguration:
        llama.cpp and Ollama serve `/v1/chat/completions` unauthenticated, and
        demanding a placeholder token would make the common local setup fail with
        a message about a credential the server does not want. OpenRouter still
        refuses without one, because there every call is billed to a key.
        """
        token = key or self.secrets.get(endpoint.secret_name)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif endpoint.is_openrouter:
            raise OpenRouterError("OpenRouter key is not configured")
        return headers

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
        path: str,
        *,
        key: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        endpoint: LlmEndpoint | None = None,
    ) -> dict[str, Any]:
        target = endpoint if endpoint is not None else self.endpoint
        headers = self._auth_headers(target, key)
        name = target.label
        # Held across attempts so the final raise can report the provider's own words
        # rather than the bare status the last attempt happened to see.
        detail = ""
        client = await self._client()
        last_attempt = RETRY_ATTEMPTS - 1
        for attempt in range(RETRY_ATTEMPTS):
            try:
                async with client.request(
                    method,
                    f"{target.origin}{path}",
                    headers=headers,
                    json=json_body,
                    params=params,
                    allow_redirects=False,
                ) as response:
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise OpenRouterError(f"{name} response exceeded the size limit")
                    retry_after = _retry_after(response)
                    if response.status in SAFE_ERROR_DETAIL_STATUSES:
                        # Auth failures are deliberately excluded: their body can echo
                        # the rejected credential. Routing and parameter errors are safe
                        # and necessary to distinguish incompatibility from an unknown
                        # model or a temporarily unavailable provider fleet.
                        detail = _provider_error(body)
                    else:
                        detail = ""
                    if response.status in RETRY_STATUSES and attempt < last_attempt:
                        await asyncio.sleep(self._retry_delay(attempt, retry_after))
                        continue
                    if response.status >= 400:
                        raise OpenRouterError(
                            f"{name} request failed with HTTP {response.status}"
                            + (f": {detail}" if detail else ""),
                            status=response.status,
                            retryable=_retryable_http_error(response.status, detail),
                            retry_after=retry_after,
                        )
                    try:
                        value = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise OpenRouterError(
                            f"{name} returned invalid JSON",
                            status=response.status,
                            retryable=True,
                        ) from exc
                    if not isinstance(value, dict):
                        raise OpenRouterError(
                            f"{name} returned an invalid response envelope",
                            status=response.status,
                            retryable=True,
                        )
                    return value
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt < last_attempt:
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue
                raise OpenRouterError(
                    f"{name} request failed: {type(exc).__name__}", retryable=True
                ) from exc
        # Only reachable when every attempt drew a retryable status, so the caller's
        # longer-horizon retry is exactly the right next move.
        raise OpenRouterError(
            f"{name} request failed" + (f": {detail}" if detail else ""), retryable=True
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

    async def test_key(
        self, candidate: str | None = None, *, endpoint: LlmEndpoint | None = None
    ) -> dict[str, Any]:
        payload = await self._request("GET", "/models", key=candidate, endpoint=endpoint)
        return {"ok": True, "models": len(payload.get("data") or [])}

    async def verify(
        self,
        *,
        endpoint: LlmEndpoint | None = None,
        model: str = "",
        max_tokens: int = 32,
    ) -> OpenRouterVerification:
        """Prove an endpoint by asking it one tiny question and keeping the answer.

        Deliberately the *plain* completion shape - no `response_format`, no
        `tools`, no routing block - because this call has one job: establish that
        the URL resolves, the credential is accepted, the model name exists, and
        something model-shaped comes back. A verify that also demanded structured
        output would fail on endpoints that work perfectly well for the assistant,
        and a verify that failed for two possible reasons is not a verify.

        The reply text is returned rather than reduced to a boolean. A server
        that answers with an empty string, a chat template leaking its own
        scaffolding, or a refusal is reachable and unusable at once, and only a
        person reading the words can tell those apart.
        """
        target = endpoint if endpoint is not None else self.endpoint
        effective = target.resolve_model(model)
        if not effective:
            raise OpenRouterError("an exact model id is required to verify an endpoint")
        started = time.monotonic()
        body: dict[str, Any] = {
            "model": effective,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Reply with exactly this sentence and nothing else: "
                        "swe-mux endpoint check ok."
                    ),
                }
            ],
            "stream": False,
            "max_tokens": max(1, int(max_tokens)),
        }
        payload = await self._request(
            "POST", "/chat/completions", json_body=body, endpoint=target
        )
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(message, dict):
            raise OpenRouterError(
                f"{target.label} returned no assistant message", status=200, retryable=True
            )
        usage = payload.get("usage") or {}
        return OpenRouterVerification(
            provider=target.provider,
            origin=target.origin,
            requested_model=effective,
            resolved_model=str(payload.get("model") or effective),
            output=(content if isinstance(content, str) else "").strip()[:VERIFY_SAMPLE_CHARS],
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=_number(usage.get("cost")),
        )

    def set_model_catalog(self, models: list[dict[str, Any]]) -> None:
        """Replace the capability cache used to shape completion requests.

        The server hydrates this from the persisted catalog on startup. A refresh
        replaces it from OpenRouter's current `/models` response. Completion still
        has bounded compatibility profiles when a configured model is absent from
        the cache, so a stale catalog cannot make an otherwise valid model unusable.
        """
        capabilities: dict[str, _ModelCapabilities] = {}
        for item in models:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            capabilities[str(item["id"])] = _model_capabilities(item)
        self._model_capabilities = capabilities

    async def models(self, *, endpoint: LlmEndpoint | None = None) -> list[dict[str, Any]]:
        target = endpoint if endpoint is not None else self.endpoint
        payload = await self._request("GET", "/models", endpoint=target)
        if not target.supports_model_catalog:
            # An OpenAI-compatible `/models` is a list of ids and nothing else: no
            # pricing, no `supported_parameters`, no modality. Running the filter
            # below over it would drop every entry - "does not advertise
            # response_format" is indistinguishable from "advertises nothing" -
            # and report an endpoint with a loaded model as having none. So the
            # ids come back bare, and the capability question is answered by the
            # verify action actually calling the thing instead.
            bare: list[dict[str, Any]] = [
                {
                    "id": str(item["id"]),
                    "name": str(item.get("id")),
                    "context_length": 0,
                    "prompt_price": None,
                    "completion_price": None,
                    "supported_parameters": [],
                }
                for item in payload.get("data") or []
                if isinstance(item, dict) and item.get("id")
            ]
            # Not cached into `_model_capabilities`: an empty capability set would
            # make `_completion_profiles` believe it had been told something.
            return bare
        result: list[dict[str, Any]] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            supported = _string_set(item.get("supported_parameters"))
            architecture = item.get("architecture")
            architecture = architecture if isinstance(architecture, dict) else {}
            modality = str(architecture.get("modality") or "")
            if "response_format" not in supported and "structured_outputs" not in supported:
                continue
            output_modalities = modality.rsplit("->", 1)[-1].split("+") if modality else []
            if output_modalities and "text" not in output_modalities:
                continue
            pricing = item.get("pricing")
            pricing = pricing if isinstance(pricing, dict) else {}
            model = {
                "id": str(item["id"]),
                "name": str(item.get("name") or item["id"]),
                "context_length": _integer(item.get("context_length")),
                "prompt_price": _number(pricing.get("prompt")),
                "completion_price": _number(pricing.get("completion")),
                "supported_parameters": sorted(supported),
            }
            reasoning = _reasoning_metadata(item.get("reasoning"))
            if reasoning is not None:
                model["reasoning"] = reasoning
            result.append(model)
        self.set_model_catalog(result)
        return result

    async def complete_json(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        reasoning_enabled: bool | None = None,
    ) -> OpenRouterResult:
        endpoint = self.endpoint
        # Resolved before the guard, not after: a custom endpoint serves one model
        # and supplies it here, so a feature whose own model slot is blank (or
        # holds an OpenRouter id that local server has never heard of) still runs.
        # Guarding on the caller's raw value first would refuse every such call.
        model = endpoint.resolve_model(model)
        if not model:
            raise OpenRouterError("an exact model id is required")
        started = time.monotonic()
        # Last line of defence, for every caller rather than each one separately.
        # A lone surrogate anywhere in the prompt makes the whole request
        # unserializable, and the resulting `UnicodeEncodeError` surfaces as the
        # caller's failure rather than as bad input — see `text_safety`.
        safe_messages = cast(list[dict[str, str]], utf8_safe_value(messages))
        request_body: dict[str, Any] = {
            "model": model,
            "messages": safe_messages,
            "stream": False,
            # `require_parameters` is the guarantee that matters: it restricts
            # routing to providers that actually honour `response_format`, so a
            # fallback still returns the schema rather than prose. Pinning to the
            # single top-ranked provider on top of that bought nothing and made
            # one provider's bad hour a total outage - every title on
            # 2026-07-31 failed with "temporarily rate-limited upstream" from
            # DeepInfra while five other providers served the same model. There
            # is no fallback to a *different model* here, so the answer cannot
            # silently change quality; only which host produced it.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        # `provider` is OpenRouter's own vocabulary for choosing between hosts of
        # one model. A single-origin endpoint has no hosts to choose between, and
        # sending it there would be asking a server that never made the promise to
        # keep it - the field is either ignored (noise in every request) or
        # rejected (a working endpoint that refuses every structured call).
        if endpoint.supports_provider_routing:
            request_body["provider"] = {"require_parameters": True, "allow_fallbacks": True}
        profiles = _completion_profiles(
            self._model_capabilities.get(model),
            max_tokens=max_tokens,
            reasoning_enabled=reasoning_enabled,
        )
        payload: dict[str, Any] | None = None
        for index, profile in enumerate(profiles):
            candidate = {**request_body, **profile}
            try:
                payload = await self._request(
                    "POST",
                    "/chat/completions",
                    json_body=candidate,
                    endpoint=endpoint,
                )
                break
            except OpenRouterError as exc:
                if index + 1 >= len(profiles) or not _is_parameter_compatibility_error(exc):
                    raise
                log.info(
                    "OpenRouter rejected completion parameter profile for model %s; "
                    "retrying with the next bounded profile (%s -> %s)",
                    model,
                    _profile_name(profile),
                    _profile_name(profiles[index + 1]),
                )
        if payload is None:  # pragma: no cover - every path returns or raises above
            raise OpenRouterError("OpenRouter did not return a completion")
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        usage = payload.get("usage") or {}
        content_type, content_length = _content_shape(content)
        generation_id = str(payload.get("id")) if payload.get("id") else None
        resolved_model = str(payload.get("model") or model)
        provider_name = str(payload.get("provider")) if payload.get("provider") else None
        finish_reason = (
            str(choice.get("finish_reason"))
            if isinstance(choice, dict) and choice.get("finish_reason")
            else None
        )
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cost_usd = _number(usage.get("cost"))
        latency_ms = int((time.monotonic() - started) * 1000)

        def malformed(message_text: str) -> OpenRouterError:
            return OpenRouterError(
                message_text,
                status=200,
                retryable=True,
                generation_id=generation_id,
                resolved_model=resolved_model,
                provider_name=provider_name,
                finish_reason=finish_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                response_content_type=content_type,
                response_content_length=content_length,
            )
        try:
            value = json.loads(content) if isinstance(content, str) else content
        except (TypeError, json.JSONDecodeError) as exc:
            raise malformed("OpenRouter structured response was missing or invalid") from exc
        if not isinstance(value, dict):
            raise malformed("OpenRouter structured response must be an object")
        return OpenRouterResult(
            generation_id=generation_id,
            requested_model=model,
            resolved_model=resolved_model,
            value=value,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            provider_name=provider_name,
            finish_reason=finish_reason,
            response_content_type=content_type,
            response_content_length=content_length,
            cached_tokens=cached_prompt_tokens(usage),
        )

    async def complete_tools(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        on_content: Callable[[str], Awaitable[None]] | None = None,
    ) -> OpenRouterToolTurn:
        """One tool-calling chat completion for the assistant's agentic loop.

        Unlike `complete_json` there is no schema to defend, so this method has
        no response_format and no parameter-profile ladder: `require_parameters`
        restricts routing to providers that honour `tools`, which is the one
        capability a malformed fallback would silently drop.

        `on_content` opts into token streaming: it is awaited with each text
        delta as it arrives, so a caller can start speaking the first sentence
        while the rest is still generating. The return value is identical either
        way — the streamed turn is reassembled into the same envelope, including
        the usage the ledger bills against. Without the callback the request
        stays unstreamed, because a buffered response is simpler to retry and
        nothing is waiting on the first token.
        """
        endpoint = self.endpoint
        model = endpoint.resolve_model(model)
        if not model:
            raise OpenRouterError("an exact model id is required")
        if max_tokens <= 0:
            raise OpenRouterError("max_tokens must be greater than zero")
        started = time.monotonic()
        safe_messages = cast(list[dict[str, Any]], utf8_safe_value(messages))
        body: dict[str, Any] = {
            "model": model,
            "messages": safe_messages,
            "stream": False,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": max_tokens,
        }
        if endpoint.supports_provider_routing:
            body["provider"] = {"require_parameters": True, "allow_fallbacks": True}
        if on_content is not None:
            payload = await self._stream_tool_completion(
                dict(body, stream=True, stream_options={"include_usage": True}),
                on_content,
                model=model,
                endpoint=endpoint,
            )
        else:
            payload = await self._request(
                "POST", "/chat/completions", json_body=body, endpoint=endpoint
            )
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            raise OpenRouterError(
                f"{endpoint.label} returned no assistant message", status=200, retryable=True
            )
        usage = payload.get("usage") or {}
        raw_calls = message.get("tool_calls")
        tool_calls = [call for call in raw_calls if isinstance(call, dict)] if isinstance(
            raw_calls, list
        ) else []
        content = message.get("content")
        return OpenRouterToolTurn(
            generation_id=str(payload.get("id")) if payload.get("id") else None,
            requested_model=model,
            resolved_model=str(payload.get("model") or model),
            content=content if isinstance(content, str) else "",
            tool_calls=tool_calls,
            message=message,
            finish_reason=(
                str(choice.get("finish_reason"))
                if isinstance(choice, dict) and choice.get("finish_reason")
                else None
            ),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=_number(usage.get("cost")),
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_name=str(payload.get("provider")) if payload.get("provider") else None,
            cached_tokens=cached_prompt_tokens(usage),
        )

    async def _stream_tool_completion(
        self,
        body: dict[str, Any],
        on_content: Callable[[str], Awaitable[None]],
        *,
        model: str,
        endpoint: LlmEndpoint | None = None,
    ) -> dict[str, Any]:
        """Consume an SSE completion and rebuild the ordinary response envelope.

        Retries follow one rule: an attempt that has already handed text to
        `on_content` cannot be retried, because the caller has spoken it and a
        second attempt would say it again. Everything before the first delta is
        an ordinary connect-or-status failure and retries normally, including
        falling back to the unstreamed request when streaming itself is what the
        provider will not do.
        """
        target = endpoint if endpoint is not None else self.endpoint
        name = target.label
        headers = {**self._auth_headers(target, None), "Accept": "text/event-stream"}
        client = await self._client()
        last_attempt = RETRY_ATTEMPTS - 1
        detail = ""
        for attempt in range(RETRY_ATTEMPTS):
            delivered = False
            try:
                async with client.request(
                    "POST",
                    f"{target.origin}/chat/completions",
                    headers=headers,
                    json=body,
                    allow_redirects=False,
                ) as response:
                    if response.status >= 400:
                        error_body = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            error_body.extend(chunk)
                            if len(error_body) > MAX_RESPONSE_BYTES:
                                break
                        retry_after = _retry_after(response)
                        detail = (
                            _provider_error(error_body)
                            if response.status in SAFE_ERROR_DETAIL_STATUSES
                            else ""
                        )
                        if response.status in RETRY_STATUSES and attempt < last_attempt:
                            await asyncio.sleep(self._retry_delay(attempt, retry_after))
                            continue
                        if response.status in STREAM_UNSUPPORTED_STATUSES:
                            # The request itself was understood and the streaming
                            # parameters were not. Answering unstreamed costs only
                            # the latency streaming exists to save.
                            raise _StreamUnsupported(detail)
                        raise OpenRouterError(
                            f"{name} request failed with HTTP {response.status}"
                            + (f": {detail}" if detail else ""),
                            status=response.status,
                            retryable=_retryable_http_error(response.status, detail),
                            retry_after=retry_after,
                        )
                    accumulator = _ToolStreamAccumulator()
                    buffer = bytearray()
                    total = 0
                    async for chunk in response.content.iter_chunked(8192):
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise OpenRouterError(
                                f"{name} response exceeded the size limit"
                            )
                        buffer.extend(chunk)
                        # unsupervised-loop-ok: splits the chunk just received;
                        # each pass consumes a line and the buffer shrinks.
                        while True:
                            newline = buffer.find(b"\n")
                            if newline < 0:
                                break
                            line = bytes(buffer[:newline])
                            del buffer[: newline + 1]
                            delta = accumulator.feed(line)
                            if delta:
                                delivered = True
                                await on_content(delta)
                    trailing = accumulator.feed(bytes(buffer))
                    if trailing:
                        delivered = True
                        await on_content(trailing)
                    if not accumulator.saw_data:
                        raise _StreamUnsupported("the completion stream carried no events")
                    return accumulator.envelope(model)
            except (aiohttp.ClientError, TimeoutError) as exc:
                if delivered:
                    raise OpenRouterError(
                        f"{name} stream broke mid-reply: {type(exc).__name__}",
                        retryable=False,
                    ) from exc
                if attempt < last_attempt:
                    await asyncio.sleep(self._retry_delay(attempt, None))
                    continue
                raise OpenRouterError(
                    f"{name} request failed: {type(exc).__name__}", retryable=True
                ) from exc
            except _StreamUnsupported as exc:
                if delivered:
                    raise OpenRouterError(
                        f"{name} ended the stream mid-reply", retryable=False
                    ) from exc
                log.info(
                    "llm streaming unavailable provider=%s model=%s reason=%s; "
                    "falling back unstreamed",
                    target.provider, model, str(exc)[:200],
                )
                return await self._request(
                    "POST",
                    "/chat/completions",
                    json_body=dict(body, stream=False),
                    endpoint=target,
                )
        raise OpenRouterError(
            f"{name} request failed" + (f": {detail}" if detail else ""), retryable=True
        )

    async def generation_cost(self, generation_id: str) -> float | None:
        endpoint = self.endpoint
        if not endpoint.supports_generation_cost:
            # `/generation` is an OpenRouter accounting API, not part of the
            # OpenAI-compatible surface. `None` here already means "unknown cost"
            # to every caller, which is the truthful answer for an endpoint that
            # bills nothing and for a proxy that bills without saying so alike -
            # and is why nothing downstream records an unpriced call as free.
            return None
        payload = await self._request(
            "GET", "/generation", params={"id": generation_id}, endpoint=endpoint
        )
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


def _retryable_http_error(status: int, detail: str) -> bool:
    if status in RETRY_STATUSES:
        return True
    normalized = detail.casefold()
    return status == 404 and any(
        phrase in normalized for phrase in TRANSIENT_MODEL_AVAILABILITY_ERRORS
    )


def _is_parameter_compatibility_error(error: OpenRouterError) -> bool:
    if error.status != 404:
        return False
    normalized = str(error).casefold()
    return any(phrase in normalized for phrase in PARAMETER_COMPATIBILITY_ERRORS)


def _reasoning_metadata(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    supported_efforts = value.get("supported_efforts")
    if isinstance(supported_efforts, list):
        result["supported_efforts"] = sorted(
            {str(effort) for effort in supported_efforts if effort is not None}
        )
    for name in ("default_effort",):
        if value.get(name) is not None:
            result[name] = str(value[name])
    for name in ("default_enabled", "supports_max_tokens", "mandatory"):
        if isinstance(value.get(name), bool):
            result[name] = value[name]
    return result or None


def _model_capabilities(item: dict[str, Any]) -> _ModelCapabilities:
    supported = frozenset(_string_set(item.get("supported_parameters")))
    reasoning = item.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    raw_efforts = reasoning.get("supported_efforts")
    efforts = (
        frozenset(str(effort) for effort in raw_efforts if effort is not None)
        if isinstance(raw_efforts, list)
        else None
    )
    return _ModelCapabilities(
        supported_parameters=supported,
        reasoning_efforts=efforts,
        reasoning_mandatory=reasoning.get("mandatory") is True,
    )


def _completion_profiles(
    capabilities: _ModelCapabilities | None,
    *,
    max_tokens: int,
    reasoning_enabled: bool | None,
) -> list[dict[str, Any]]:
    """Build request variants without ever weakening schema or output bounds."""
    if max_tokens <= 0:
        raise OpenRouterError("OpenRouter max_tokens must be greater than zero")

    if capabilities is None:
        token_parameters = list(TOKEN_LIMIT_PARAMETERS)
    else:
        token_parameters = [
            name for name in TOKEN_LIMIT_PARAMETERS if name in capabilities.supported_parameters
        ]
        # OpenRouter's cached model catalog can lag a newly configured exact model.
        # Advertised capabilities guide the first request, but a missing token field
        # must not force an unbounded call or reject a model the router can handle.
        if not token_parameters:
            token_parameters = list(TOKEN_LIMIT_PARAMETERS)

    reasoning_profiles: list[dict[str, Any]] = [{}]
    if reasoning_enabled is True:
        if capabilities is not None and not (
            {"reasoning", "reasoning_effort"} & capabilities.supported_parameters
        ):
            raise OpenRouterError(
                "The selected OpenRouter model does not support reasoning controls"
            )
        reasoning_profiles = [{"reasoning": {"enabled": True}}]
    elif reasoning_enabled is False:
        can_disable = capabilities is None or (
            not capabilities.reasoning_mandatory
            and bool(
                {"reasoning", "reasoning_effort"}
                & capabilities.supported_parameters
            )
            and (
                capabilities.reasoning_efforts is None
                or "none" in capabilities.reasoning_efforts
            )
        )
        if can_disable:
            # Omitting this on the fallback supports non-reasoning models and older
            # endpoints whose model-level catalog overstates reasoning support.
            reasoning_profiles = [{"reasoning": {"effort": "none"}}, {}]

    profiles: list[dict[str, Any]] = []
    for token_parameter in token_parameters:
        for reasoning in reasoning_profiles:
            profiles.append({token_parameter: max_tokens, **reasoning})
    return profiles


def _profile_name(profile: dict[str, Any]) -> str:
    token_parameter = next(
        (name for name in TOKEN_LIMIT_PARAMETERS if name in profile), "bounded-output"
    )
    reasoning = profile.get("reasoning")
    if not isinstance(reasoning, dict):
        return token_parameter
    if reasoning.get("effort") == "none":
        return f"{token_parameter}+reasoning-disabled"
    return f"{token_parameter}+reasoning"


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if item is not None}


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _content_shape(value: Any) -> tuple[str, int]:
    """Return safe response diagnostics without retaining provider output."""
    if value is None:
        return "null", 0
    if isinstance(value, str):
        return "string", len(value.encode("utf-8", errors="replace"))
    if isinstance(value, dict):
        return "object", len(value)
    if isinstance(value, list):
        return "array", len(value)
    return type(value).__name__, 1


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def cached_prompt_tokens(usage: Any) -> int:
    """Prompt tokens the provider served from cache, from one usage payload.

    OpenRouter normalises every provider onto the OpenAI shape
    (`usage.prompt_tokens_details.cached_tokens`), and some upstreams also put a
    bare `cached_tokens` at the top level. Both are read because a caller that
    saw only one of them would report "no caching" for exactly the providers
    that report it the other way, which is indistinguishable from a real miss.
    """
    if not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        return max(0, _integer(details.get("cached_tokens")))
    return max(0, _integer(usage.get("cached_tokens")))


def needs_explicit_cache_control(
    model: str, *, cache_policy: CachePolicy = "by_model"
) -> bool:
    """Whether this model's provider caches only where a breakpoint says to.

    Anthropic-routed models cache nothing without an explicit `cache_control`
    breakpoint in the request; OpenAI-family, DeepSeek, and the other implicit
    cachers ignore the marker and cache a repeated prefix on their own. The
    routing prefix of an OpenRouter model id is the whole question - a variant
    suffix (`:beta`, `:floor`) rides on the same provider.

    That reading only works while the id *is* an OpenRouter route. A custom
    endpoint's model id is whatever its operator loaded - `qwen2.5-coder:7b`, a
    filesystem path, or, quite legitimately, `anthropic/claude-sonnet-4.5`
    proxied by something that has never heard of `cache_control`. So the prefix
    is not consulted at all under `cache_policy="unknown"`: no breakpoint is
    sent, and - the half that would otherwise fail silently - no implicit hit is
    assumed either, so a zero in the ledger reads as unmeasured rather than as a
    caching regression somebody should go and investigate.
    """
    if cache_policy == "unknown":
        return False
    return str(model).split("/", 1)[0].strip().lower() in EXPLICIT_CACHE_CONTROL_PROVIDERS


def cache_stable_message(
    message: dict[str, Any], *, model: str, cache_policy: CachePolicy = "by_model"
) -> dict[str, Any]:
    """Mark one message as the end of the request's cache-stable prefix.

    Returns the message unchanged for providers that cache implicitly and for
    providers whose caching is unknown, so the marked prompt is only ever sent
    where it is understood. The breakpoint covers everything *before* it too -
    for Anthropic that ordering is tools, then system, then messages, so marking
    the primer caches the tool definitions with it and no second breakpoint is
    needed for them.

    Marking is deliberately the caller's choice rather than this module's: only
    the caller knows which of its messages is stable across calls, and a
    breakpoint placed above something that changes every round is a cache write
    billed at a premium that is never read back.
    """
    if not needs_explicit_cache_control(model, cache_policy=cache_policy):
        return message
    content = message.get("content")
    if isinstance(content, str) and content:
        parts: list[dict[str, Any]] = [{"type": "text", "text": content}]
    elif isinstance(content, list) and content:
        parts = [dict(part) for part in content if isinstance(part, dict)]
        if len(parts) != len(content):
            return message  # a shape this function did not build; leave it alone
    else:
        return message
    parts[-1] = {**parts[-1], "cache_control": {"type": "ephemeral"}}
    return {**message, "content": parts}
