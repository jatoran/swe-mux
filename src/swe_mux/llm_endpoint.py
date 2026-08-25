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

Three properties are never *assumed* about a custom endpoint:

- **Cost.** A local server bills nothing and reports nothing; a paid proxy bills
  and may still report nothing. `usage.cost` absent means unknown, never zero.
- **Routing.** `provider: {require_parameters, allow_fallbacks}` is OpenRouter
  vocabulary. Sending it to Ollama asks for a guarantee nobody made.
- **Caching.** This is the one that would fail silently. `marks_cache_breakpoints`
  answers from the endpoint rather than the model, because OpenRouter translates
  a cache marker into whatever the routed provider understands and a custom
  server has nothing in front of it doing that. An unproven endpoint's cache
  behaviour is `unknown`: no breakpoint is sent (an OpenAI-compatible server has
  no reason to accept Anthropic's content-part extension) and no implicit hit is assumed, so a
  zero in the ledger reads as "not reported" rather than as a regression.

They are **measured instead of guessed.** The pessimistic profile above is right
for Ollama and wrong for an OpenRouter-shaped proxy - LiteLLM, or a personal
gateway - which can serve the annotated catalog and report cost per call. Held as
a fixed per-provider table, that profile cost a capable endpoint its model picker,
all of its pricing, and its whole cache ledger, for no reason other than sharing a
provider id with llama.cpp. So `EndpointCapabilities` records what the endpoint
actually *did* during verification, and the flags below are read from that record
rather than from a constant. What was never probed keeps the pessimistic answer,
which is the only safe direction for a guess: an unmeasured endpoint behaves
exactly as every custom endpoint did before this existed.

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
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

__all__ = [
    "LLM_PROVIDERS",
    "MAX_BASE_URL_CHARS",
    "OPENROUTER_CAPABILITIES",
    "OPENROUTER_ORIGIN",
    "UNPROVEN_CAPABILITIES",
    "CachePolicy",
    "CapabilityStore",
    "CatalogShape",
    "EndpointCapabilities",
    "LlmEndpoint",
    "LlmReadiness",
    "base_url_error",
    "capabilities_of_record",
    "catalog_url_error",
    "normalize_base_url",
    "normalize_catalog_url",
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

#: What an endpoint's `/models` turned out to be worth.
#:
#: - ``annotated`` - OpenRouter's own catalog shape: every entry carries
#:   ``supported_parameters``, ``architecture``, and a ``pricing`` block. Enough
#:   to drive the model picker, to price a call, and to shape a request from a
#:   model's advertised capabilities.
#: - ``bare`` - an OpenAI-compatible ``/models``: a list of ids and nothing else.
#:   A picker can list them; nothing can price them.
#: - ``none`` - no usable ``/models`` at all. The endpoint serves whatever single
#:   model its operator loaded, and that has to be typed in by hand.
CatalogShape = Literal["none", "bare", "annotated"]

#: The secret-store names holding each provider's bearer token. A custom endpoint
#: frequently has none - llama.cpp and Ollama accept any string or no header at
#: all - so an absent key is a configuration, not an error.
SECRET_NAMES: dict[str, str] = {
    "openrouter": "openrouter_api_key",
    "custom": "custom_llm_api_key",
}


@dataclass(frozen=True, slots=True)
class EndpointCapabilities:
    """What one endpoint proved it could do, measured rather than assumed.

    Every field is the answer to a question that was previously hardcoded per
    provider, and every one of them was wrong for at least one legitimate
    endpoint. The record is written by the verify action - the one place that
    already makes a real call to the configured URL with the configured
    credential - and is fingerprinted alongside it, so editing the endpoint
    discards the measurement exactly as it discards the proof.

    The defaults are the pessimistic answer on purpose. An endpoint nobody has
    probed must behave as though it can do nothing special, because every
    optimistic default here fails *silently*: a cache marker sent to a server
    that ignores it reports zero cached tokens, which reads as a caching
    regression rather than as an unanswered question.
    """

    catalog: CatalogShape = "none"

    reports_cost: bool = False
    """Whether a completion's `usage.cost` came back populated.

    Distinct from `LlmEndpoint.supports_generation_cost`, which is about
    OpenRouter's separate `/generation` accounting API. A proxy can report cost
    per call and have no `/generation` at all, and conflating the two made the
    budget controls tell such an endpoint it could not bind a dollar cap.
    """

    reports_cache: bool = False
    """Whether the usage payload carried `prompt_tokens_details` at all.

    The presence of the *key* is the signal, never its value: a zero
    `cached_tokens` from a provider that reports caching is a real miss, and a
    zero from one that does not report it is silence. Those two readings are
    indistinguishable downstream unless this is recorded here.
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "catalog": self.catalog,
            "reports_cost": self.reports_cost,
            "reports_cache": self.reports_cache,
        }

    @classmethod
    def from_dict(cls, value: Any) -> EndpointCapabilities:
        """Rebuild a record from storage, falling back to unproven on anything odd.

        Deliberately total: this parses a JSON column that a previous version of
        swe-mux never wrote and a future one may have extended, and the honest
        recovery from either is the pessimistic default rather than a daemon that
        will not answer.
        """
        if not isinstance(value, dict):
            return UNPROVEN_CAPABILITIES
        catalog = value.get("catalog")
        return cls(
            catalog=catalog if catalog in ("none", "bare", "annotated") else "none",
            reports_cost=value.get("reports_cost") is True,
            reports_cache=value.get("reports_cache") is True,
        )


#: What is assumed about an endpoint nobody has proven. Identical to the fixed
#: profile every custom endpoint carried before capabilities existed, which is
#: what makes this change inert for an install that never re-verifies.
UNPROVEN_CAPABILITIES = EndpointCapabilities()

#: OpenRouter's own, known rather than probed. It is the origin swe-mux ships
#: against, its catalog shape is the one every other endpoint is compared to, and
#: spending a verify round-trip to rediscover that would only add a way for the
#: default provider to appear broken.
OPENROUTER_CAPABILITIES = EndpointCapabilities(
    catalog="annotated", reports_cost=True, reports_cache=True
)


def capabilities_of_record(record: Any) -> EndpointCapabilities:
    """The measurement stored on one verification row, or the unproven default.

    Total on purpose, the same way `EndpointCapabilities.from_dict` is: the
    column is empty for every row written before capabilities existed, absent
    entirely on a database this daemon has not migrated yet, and could hold
    anything at all if hand-edited. Every one of those is answered with the
    profile that endpoint already behaved as, rather than with an exception on
    the readiness path.
    """
    if not isinstance(record, dict):
        return UNPROVEN_CAPABILITIES
    raw = record.get("capabilities_json")
    if not isinstance(raw, str) or not raw.strip():
        return UNPROVEN_CAPABILITIES
    try:
        return EndpointCapabilities.from_dict(json.loads(raw))
    except (TypeError, ValueError):
        return UNPROVEN_CAPABILITIES


class CapabilityStore:
    """The capability records the per-request endpoint resolver reads.

    A cache with a specific job. `LlmEndpoint` is rebuilt from `Config` on every
    request - that is what lets a corrected base URL take effect on the call that
    tests it - and that resolution is synchronous, while the durable record lives
    in SQLite behind an async store. Rather than make endpoint resolution async
    everywhere it is used, the daemon hydrates this at startup and refreshes it
    at the three moments that can change the answer: a verification, a key write,
    and an endpoint edit.

    Empty is not an error state. A miss yields `UNPROVEN_CAPABILITIES`, so a
    daemon that has not hydrated yet behaves exactly like one talking to an
    endpoint nobody proved.
    """

    __slots__ = ("_by_provider",)

    def __init__(self) -> None:
        self._by_provider: dict[str, EndpointCapabilities] = {}

    def get(self, provider: str) -> EndpointCapabilities:
        return self._by_provider.get(provider, UNPROVEN_CAPABILITIES)

    def set(self, provider: str, capabilities: EndpointCapabilities) -> None:
        self._by_provider[provider] = capabilities

    def clear(self, provider: str | None = None) -> None:
        if provider is None:
            self._by_provider.clear()
        else:
            self._by_provider.pop(provider, None)


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
    """Whether `/generation` can be asked what a completion actually cost.

    OpenRouter's own accounting API, not part of the OpenAI-compatible surface,
    so this stays true only for OpenRouter itself. Whether an endpoint reports
    cost *at all* is the separate and more commonly interesting question, and it
    is `reports_cost` below.
    """

    requires_verification: bool
    """Whether a durable verified record gates the features that depend on this."""

    label: str

    reports_cost: bool = True
    """Whether a completion here comes back with its own `usage.cost`.

    Split from `supports_generation_cost` because a proxy can do one without the
    other, and the surfaces that care are the budget controls: a dollar-only cap
    cannot bind against an endpoint that reports nothing, so the control says so
    and offers the token axis instead (`budget.py`).
    """

    capabilities: EndpointCapabilities = UNPROVEN_CAPABILITIES
    """The measurement the four flags above were derived from.

    Carried rather than discarded so a surface can say *why* an endpoint has no
    picker or no prices - "its `/models` returned a bare id list" is actionable
    where "no catalog" is not.
    """

    catalog_override: str = ""
    """Where this endpoint's model catalog lives, when it is not `origin/models`.

    Blank derives it, which is right for every OpenAI-compatible server and for a
    gateway serving the catalog beside its chat route. A separate value exists
    because a catalog is not always published by the thing serving completions,
    and because an operator with a server that publishes none at all may point at
    a document they wrote themselves naming and pricing what it actually loads.

    Held as an absolute URL rather than a base, so nothing is appended: the
    catalog may legitimately live at `/api/models`, at a static JSON file, or
    behind a query string that bounds the page.
    """

    @property
    def is_openrouter(self) -> bool:
        return self.provider == "openrouter"

    @property
    def catalog_url(self) -> str:
        """The exact URL a catalog fetch goes to."""
        return self.catalog_override or f"{self.origin}/models"

    @property
    def pins_one_model(self) -> bool:
        """Whether every caller's model id is redirected to this endpoint's own.

        True for the single-model case a custom endpoint usually is, and false
        once a catalog proves there is something to choose between - which is
        what lets the per-feature model settings mean what they say against a
        capable proxy instead of all collapsing onto one pin.
        """
        return bool(self.model_override)

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
            [
                self.provider,
                self.origin,
                self.model_override,
                # Covered because the capability record is measured *through* it:
                # repoint the catalog and what was proven about this endpoint was
                # proven about a different document.
                self.catalog_override,
                api_key or "",
            ]
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
        reports_cost=True,
        capabilities=OPENROUTER_CAPABILITIES,
    )


def custom_endpoint(
    *,
    base_url: str,
    model: str,
    catalog_url: str = "",
    capabilities: EndpointCapabilities = UNPROVEN_CAPABILITIES,
) -> LlmEndpoint:
    """A bring-your-own endpoint, shaped by whatever it proved it could do.

    The three OpenRouter-specific behaviours all hang off one signal: an endpoint
    whose `/models` came back in OpenRouter's *annotated* shape is OpenRouter, or
    is relaying it faithfully enough to be treated as such. Nothing else serves
    `supported_parameters` beside `pricing.input_cache_read` - Ollama, llama.cpp,
    vLLM, and LM Studio all answer with bare ids - so the inference cannot
    mistake a local server for a router, which is the direction that would hurt.

    Caching is gated on the same signal rather than on `reports_cache`, and the
    distinction is worth keeping straight: reporting cache numbers says the usage
    payload is OpenRouter-shaped, while *translating* a `cache_control` marker to
    whatever the routed provider wants is a thing only OpenRouter (or a
    passthrough to it) does. An endpoint could plausibly do the first and not the
    second, and marking a prefix nobody honours writes no cache while reporting
    zero hits - the silent failure this whole record exists to avoid.
    """
    annotated = capabilities.catalog == "annotated"
    return LlmEndpoint(
        provider="custom",
        origin=normalize_base_url(base_url),
        secret_name=SECRET_NAMES["custom"],
        # A catalog is a set of models to choose between, so the per-feature
        # model settings stop being ids this server never heard of and start
        # meaning what they say. Without one there is nothing to pick from and
        # the single configured model is the only thing that can be requested.
        model_override="" if annotated else str(model or "").strip(),
        cache_policy="by_model" if annotated else "unknown",
        supports_provider_routing=annotated,
        supports_model_catalog=annotated,
        # `/generation` stays OpenRouter's alone. A proxy that reports cost in
        # `usage` still has no such endpoint, and asking would 404 every call.
        supports_generation_cost=False,
        requires_verification=True,
        label="Custom OpenAI-compatible endpoint",
        reports_cost=capabilities.reports_cost,
        capabilities=capabilities,
        catalog_override=normalize_catalog_url(catalog_url),
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


def normalize_catalog_url(value: str) -> str:
    """Trim a catalog URL. Unlike a base URL, its path is used exactly as given."""
    return str(value or "").strip()


def catalog_url_error(value: str) -> str | None:
    """Why this catalog URL cannot be used, or `None`. Blank is legal.

    Deliberately looser than `base_url_error` in exactly one way: a query string
    is allowed, because nothing is appended to this URL and a catalog may
    legitimately need one to page or to bound itself. Every other rejection is
    the same and for the same reason - a scheme aiohttp will not dial, or
    credentials that would be logged along with the URL.
    """
    raw = normalize_catalog_url(value)
    if not raw:
        return None
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
    try:
        _ = parts.port
    except ValueError:
        return "has an invalid port"
    return None


def model_error(value: str) -> str | None:
    """Why this model id is malformed, or `None`.

    Blank is **not** an error here, and that is a deliberate move rather than a
    relaxation. Whether a blank model can be tolerated depends on whether the
    endpoint serves a catalog to choose from, and that is a measured property
    living in a SQLite row - which `validate_config` cannot see, being both
    synchronous and config-only. Refusing blank here meant the answer was
    hardcoded to "no catalog", which is the wrong answer for every OpenRouter-
    shaped proxy.

    So the requirement moved to `readiness`, which *does* have the capability
    record, and states it as `no_model` with prose a surface can render. A
    validation error and a not-ready reason are two ways of saying the same
    thing, and only one of them can be conditional on something the validator
    cannot reach.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) > MAX_MODEL_CHARS:
        return f"must be at most {MAX_MODEL_CHARS} characters"
    if any(character.isspace() for character in raw):
        return "must not contain whitespace"
    return None


def resolve_endpoint(
    config: Any, capabilities: EndpointCapabilities | CapabilityStore | None = None
) -> LlmEndpoint:
    """The endpoint this install's configuration selects.

    Falls back to OpenRouter for an unrecognised provider id rather than raising:
    `validate_config` refuses to save one, so reaching here with a bad value means
    a hand-edited file, and the honest recovery is the default everyone else has
    rather than a daemon that will not answer.

    `capabilities` may be a record or the store to look one up in, because the
    callers differ in what they hold: the daemon owns a store and resolves per
    request, while a test or a one-off wants to state a record outright. Omitting
    it yields the unproven profile, which is what every caller had before
    capabilities existed.
    """
    provider = str(getattr(config, "llm_provider", "openrouter") or "openrouter")
    if provider != "custom":
        return openrouter_endpoint()
    if isinstance(capabilities, CapabilityStore):
        measured = capabilities.get(provider)
    else:
        measured = capabilities or UNPROVEN_CAPABILITIES
    return custom_endpoint(
        base_url=str(getattr(config, "custom_llm_base_url", "") or ""),
        model=str(getattr(config, "custom_llm_model", "") or ""),
        catalog_url=str(getattr(config, "custom_llm_catalog_url", "") or ""),
        capabilities=measured,
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
    reports_cost = endpoint.reports_cost
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
    # Asked only where there is nothing to choose from. An endpoint serving the
    # annotated catalog has a picker behind it and each feature's own model
    # setting means what it says, so demanding a single pinned id as well would
    # refuse a perfectly configured install for the sake of a field it stopped
    # needing. This is the requirement `model_error` cannot state, because
    # whether it applies is a measured property rather than a config one.
    if not endpoint.supports_model_catalog and not endpoint.model_override:
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
        # What the proving call measured, so a surface can explain an absent
        # picker or absent prices instead of only showing their absence. Read off
        # the endpoint rather than the row: for OpenRouter there is no row at all
        # and the answer is still known.
        "capabilities": endpoint.capabilities.as_dict(),
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
