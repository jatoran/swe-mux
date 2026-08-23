"""Which language-model endpoint the install talks to, and whether it is proven.

swe-mux's speech and hearing are already local: faster-whisper decodes on this
machine and Kokoro synthesizes on it. The language model was the one part that
had to be somebody else's server, because `OpenRouterClient` hard-coded a single
origin. This module is the seam that removes that: one `LlmEndpoint` describes
*where* a completion goes, and the client asks it rather than a constant.

Two endpoints exist today and the shape covers both:

- **`openrouter`** - the default, unchanged for everyone who never opens this
  screen. It knows the catalog, reports cost, and honours OpenRouter's own
  routing directives.
- **`custom`** - any OpenAI-compatible `/chat/completions`, which is llama.cpp,
  Ollama, vLLM, and LM Studio with one shape and one set of three fields
  (`base_url`, `api_key`, `model`). It advertises none of the above, and this
  module is where saying so lives instead of each caller guessing.

Three properties are deliberately *not* inferred from a custom endpoint:

- **Cost.** A local server bills nothing and reports nothing; a paid proxy bills
  and may still report nothing. `usage.cost` absent means unknown, never zero.
- **Routing.** `provider: {require_parameters, allow_fallbacks}` is OpenRouter
  vocabulary. Sending it to Ollama asks for a guarantee nobody made.
- **Caching.** This is the one that would fail silently. `marks_cache_breakpoints`
  answers from the endpoint rather than the model, because OpenRouter translates
  a cache marker into whatever the routed provider understands and a custom
  server has nothing in front of it doing that. A custom endpoint's cache
  behaviour is `unknown`: no breakpoint is sent (an OpenAI-compatible server has
  no reason to accept Anthropic's content-part extension) and no implicit hit is assumed, so a
  zero in the ledger reads as "not reported" rather than as a regression.

**Verification** is the other half. An OpenRouter key is tested at the moment it
is stored, against an origin swe-mux ships; there is nothing further to prove and
nothing changes for existing installs. A custom endpoint is an arbitrary host
speaking an approximate dialect, where a typo in `base_url` or a model name the
server has never heard of produces exactly one symptom - an automation that looks
enabled and quietly fails hours later. So it must pass one real completion before
anything is allowed to depend on it, and the record of that pass is fingerprinted
over the whole triple: change the URL, the key, or the model and the fingerprint
no longer matches, which is what makes "editing the endpoint un-verifies it" a
property of the data rather than a rule every write path has to remember.

The origin is **install configuration, never a request parameter.** No caller,
agent, or MCP tool can name a URL here; that stays on the decision-gated list
(`.docs/development/ROADMAP.md`, "Decision-gated capabilities"), and this module
is the boundary that keeps the distinction real.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

__all__ = [
    "LLM_PROVIDERS",
    "MAX_BASE_URL_CHARS",
    "OPENROUTER_ORIGIN",
    "CachePolicy",
    "LlmEndpoint",
    "LlmReadiness",
    "base_url_error",
    "normalize_base_url",
    "openrouter_endpoint",
    "readiness",
    "resolve_endpoint",
]

OPENROUTER_ORIGIN = "https://openrouter.ai/api/v1"
LLM_PROVIDERS: tuple[str, ...] = ("openrouter", "custom")
MAX_BASE_URL_CHARS = 300
MAX_MODEL_CHARS = 200

#: How this endpoint's provider caches a repeated prompt prefix.
#:
#: - ``by_model`` - routed through OpenRouter, which normalises cache markers
#:   across providers, so a breakpoint is understood wherever the call lands
#:   (`marks_cache_breakpoints`).
#: - ``unknown`` - not answerable at all. Send no breakpoint, assume no implicit
#:   hit, and report a zero as unmeasured rather than as a miss.
CachePolicy = Literal["by_model", "unknown"]

#: The secret-store names holding each provider's bearer token. A custom endpoint
#: frequently has none - llama.cpp and Ollama accept any string or no header at
#: all - so an absent key is a configuration, not an error.
SECRET_NAMES: dict[str, str] = {
    "openrouter": "openrouter_api_key",
    "custom": "custom_llm_api_key",
}


@dataclass(frozen=True, slots=True)
class LlmEndpoint:
    """Everything a completion needs to know about where it is going.

    Constructed from `Config` plus the secret store on each request rather than
    once at daemon start, so switching providers or fixing a typo'd URL takes
    effect on the next call instead of on the next restart.
    """

    provider: str
    """`openrouter` or `custom`. The identity the verification record is keyed by."""

    origin: str
    """Base URL with no trailing slash. Endpoint paths are appended to it verbatim."""

    secret_name: str
    """Where the bearer token lives, when there is one."""

    model_override: str
    """The single model this endpoint serves, or `""` when the caller's id is used.

    A custom endpoint is one server with one loaded model far more often than
    not, and every model setting in the app names an OpenRouter id that server
    has never heard of. Redirecting at the seam is what lets the assistant, the
    scan timeline, and the titler all work against a local model without each of
    them learning about providers.
    """

    cache_policy: CachePolicy
    supports_provider_routing: bool
    """Whether OpenRouter's `provider` routing block means anything here."""

    supports_model_catalog: bool
    """Whether `/models` returns OpenRouter's annotated catalog (pricing, capabilities)."""

    supports_generation_cost: bool
    """Whether `/generation` can be asked what a completion actually cost."""

    requires_verification: bool
    """Whether a durable verified record gates the features that depend on this."""

    label: str

    @property
    def is_openrouter(self) -> bool:
        return self.provider == "openrouter"

    def resolve_model(self, requested: str) -> str:
        """The model id to actually send, given what a caller asked for."""
        return self.model_override or str(requested or "")

    def fingerprint(self, api_key: str | None) -> str:
        """An opaque digest of everything an edit could change.

        Covers the key as well as the URL and the model, so replacing a rotated
        token un-verifies the endpoint exactly as changing its address does -
        both are equally capable of turning a working endpoint into a silent
        401. Nothing key-shaped is recoverable from the result, and the record
        that stores it holds no other copy of the secret.
        """
        material = "\n".join(
            [self.provider, self.origin, self.model_override, api_key or ""]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def openrouter_endpoint() -> LlmEndpoint:
    """The default endpoint. Identical to what every caller had before this module."""
    return LlmEndpoint(
        provider="openrouter",
        origin=OPENROUTER_ORIGIN,
        secret_name=SECRET_NAMES["openrouter"],
        model_override="",
        cache_policy="by_model",
        supports_provider_routing=True,
        supports_model_catalog=True,
        supports_generation_cost=True,
        # Storing an OpenRouter key already tests it against this exact origin
        # (`operation=set` calls `test_key` first), so configuring it *is*
        # verifying it. Requiring a second, separate act here would switch off
        # every existing install's automations on upgrade to prove something
        # they had already proven.
        requires_verification=False,
        label="OpenRouter",
    )


def custom_endpoint(*, base_url: str, model: str) -> LlmEndpoint:
    return LlmEndpoint(
        provider="custom",
        origin=normalize_base_url(base_url),
        secret_name=SECRET_NAMES["custom"],
        model_override=str(model or "").strip(),
        cache_policy="unknown",
        supports_provider_routing=False,
        supports_model_catalog=False,
        supports_generation_cost=False,
        requires_verification=True,
        label="Custom OpenAI-compatible endpoint",
    )


def normalize_base_url(value: str) -> str:
    """Trim a base URL to the form request paths are appended to.

    Only whitespace and trailing slashes are removed. Nothing is *repaired* -
    a URL that does not validate is rejected by `base_url_error` rather than
    guessed at, because guessing produces an endpoint that verifies against a
    host the operator did not name.
    """
    return str(value or "").strip().rstrip("/")


def base_url_error(value: str) -> str | None:
    """Why this base URL cannot be used, or `None` when it can.

    Deliberately permissive about *where*: a local llama.cpp, a LAN vLLM box, and
    a hosted proxy are all legitimate, and an allowlist here would only stop the
    people this feature exists for. Deliberately strict about *shape*, because
    every rejected form is one that fails confusingly rather than loudly - a
    scheme aiohttp will not dial, credentials that would be logged with the URL,
    or a query string silently dropped when a path is appended.
    """
    raw = normalize_base_url(value)
    if not raw:
        return "a base URL is required"
    if len(raw) > MAX_BASE_URL_CHARS:
        return f"must be at most {MAX_BASE_URL_CHARS} characters"
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "must be a valid URL"
    if parts.scheme not in {"http", "https"}:
        return "must start with http:// or https://"
    if not parts.hostname:
        return "must include a host"
    if "@" in parts.netloc:
        return "must not embed credentials; put the key in the API key field"
    if parts.query or parts.fragment:
        return "must not carry a query string or fragment"
    try:
        # `urlsplit` defers port parsing, so a garbage port only raises on access.
        _ = parts.port
    except ValueError:
        return "has an invalid port"
    return None


def model_error(value: str) -> str | None:
    """Why this model id cannot be used, or `None`.

    A *pin* rather than an override, in `modelRouting.ts` vocabulary: blank is a
    validation error, not a fall-through, because there is no routed default a
    custom endpoint could inherit. Every model setting in the app names an
    OpenRouter id, and none of them mean anything to a local server.
    """
    raw = str(value or "").strip()
    if not raw:
        return "a model id is required"
    if len(raw) > MAX_MODEL_CHARS:
        return f"must be at most {MAX_MODEL_CHARS} characters"
    if any(character.isspace() for character in raw):
        return "must not contain whitespace"
    return None


def resolve_endpoint(config: Any) -> LlmEndpoint:
    """The endpoint this install's configuration selects.

    Falls back to OpenRouter for an unrecognised provider id rather than raising:
    `validate_config` refuses to save one, so reaching here with a bad value means
    a hand-edited file, and the honest recovery is the default everyone else has
    rather than a daemon that will not answer.
    """
    provider = str(getattr(config, "llm_provider", "openrouter") or "openrouter")
    if provider != "custom":
        return openrouter_endpoint()
    return custom_endpoint(
        base_url=str(getattr(config, "custom_llm_base_url", "") or ""),
        model=str(getattr(config, "custom_llm_model", "") or ""),
    )


@dataclass(frozen=True, slots=True)
class LlmReadiness:
    """Whether model-backed features may run, and the sentence that says why not.

    `reason` is the whole point of the type. An unverified provider that made a
    switch inert without saying so would be the silent downstream failure this
    replaces, so every not-ready state carries prose a surface can render
    verbatim, and `code` is what the browser branches on.
    """

    ready: bool
    provider: str
    code: str
    reason: str
    reports_cost: bool = True
    """Whether this endpoint tells swe-mux what a completion cost.

    Carried here rather than derived in the browser because it is a property of
    the endpoint, and the surfaces that need it are the budget controls: a
    dollar-only cap cannot bind against a provider that reports nothing, so the
    control says so and offers the token axis as the backstop (`budget.py`).
    Distinct from `ready` in both directions - an unverified endpoint can still
    be one that would report cost, and a perfectly ready local server never
    will.
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "provider": self.provider,
            "code": self.code,
            "reason": self.reason,
            "reports_cost": self.reports_cost,
        }


def readiness(
    endpoint: LlmEndpoint,
    *,
    api_key: str | None,
    verified_fingerprint: str | None,
    verification_exists: bool = False,
) -> LlmReadiness:
    """Resolve one endpoint's state into ready-or-the-reason-it-is-not.

    The order of the checks is the order a person fixes them in: a missing
    endpoint before a missing key, a missing key before an unproven one, and
    "you changed it since you proved it" distinguished from "you never proved
    it" because they are different sentences and only one of them is a surprise.
    """
    reports_cost = endpoint.supports_generation_cost
    if endpoint.provider == "openrouter":
        if not api_key:
            return LlmReadiness(
                False,
                endpoint.provider,
                "no_key",
                "No OpenRouter API key is configured, so nothing model-backed can run.",
                reports_cost,
            )
        return LlmReadiness(
            True, endpoint.provider, "ready", "OpenRouter key configured.", reports_cost
        )

    if not endpoint.origin:
        return LlmReadiness(
            False,
            endpoint.provider,
            "no_endpoint",
            "The custom model endpoint has no base URL yet.",
            reports_cost,
        )
    if not endpoint.model_override:
        return LlmReadiness(
            False,
            endpoint.provider,
            "no_model",
            "The custom model endpoint has no model id yet.",
            reports_cost,
        )
    if not verified_fingerprint:
        return LlmReadiness(
            False,
            endpoint.provider,
            "unverified",
            "The custom model endpoint has not been verified yet. "
            "Verify it in Settings → Accounts to see one real reply from it.",
            reports_cost,
        )
    if verified_fingerprint != endpoint.fingerprint(api_key):
        return LlmReadiness(
            False,
            endpoint.provider,
            "endpoint_changed",
            "The custom model endpoint changed since it was verified. "
            "Verify it again in Settings → Accounts.",
            reports_cost,
        )
    return LlmReadiness(
        True,
        endpoint.provider,
        "ready",
        f"Verified against {endpoint.origin}.",
        reports_cost,
    )


def verification_state(
    endpoint: LlmEndpoint,
    *,
    api_key: str | None,
    record: dict[str, Any] | None,
) -> dict[str, Any]:
    """The durable verification record, judged against the current configuration.

    `verified` is never read off the row. It is recomputed by comparing the
    stored fingerprint with the live one, which is what makes an edit un-verify
    an endpoint even when the edit arrived by hand in `config.toml` and no write
    path in this daemon ran at all.
    """
    stored = str((record or {}).get("fingerprint") or "")
    current = endpoint.fingerprint(api_key)
    return {
        "provider": endpoint.provider,
        "verified": bool(stored) and stored == current,
        # A row that no longer matches is more useful than no row: "you changed
        # it" and "you never did it" are different problems.
        "stale": bool(stored) and stored != current,
        "verified_at": (record or {}).get("verified_at"),
        "base_url": (record or {}).get("base_url") or "",
        "model": (record or {}).get("model") or "",
        "resolved_model": (record or {}).get("resolved_model") or "",
        "sample": (record or {}).get("sample") or "",
        "latency_ms": (record or {}).get("latency_ms") or 0,
    }
