"""What an endpoint proved it could do, and what is assumed when nothing has.

`test_custom_llm_endpoint.py` holds the rules a bring-your-own endpoint follows.
This holds the newer question underneath them: *how those rules are decided*.

The rules used to be a fixed table keyed by provider id, which was right for
Ollama and wrong for every OpenRouter-shaped proxy - such an endpoint lost its
model picker, all of its pricing, and its whole cache ledger for no reason other
than sharing a provider id with llama.cpp. So they are measured at verify time
instead, and three properties are worth holding here because each is a way the
measurement could look fine and be wrong:

- **The unmeasured answer is the old answer, field for field.** Nobody
  re-verifies on upgrade, so if the pessimistic default drifted from the table it
  replaced, the upgrade itself would be a silent behaviour change.
- **The optimistic branch needs real evidence.** Everything OpenRouter-specific
  hangs off one signal - an annotated catalog - and a single annotated row among
  bare ones must not trip it, because that is the direction that sends routing
  directives and cache markers to a server that understands neither.
- **An edit discards the measurement.** A base URL changed from a proxy to a
  local server keeps its verification row only until the fingerprint is checked,
  but the capability record is read *before* any of that, on the resolution path.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from swe_mux.config import Config
from swe_mux.llm_endpoint import (
    OPENROUTER_CAPABILITIES,
    UNPROVEN_CAPABILITIES,
    CapabilityStore,
    EndpointCapabilities,
    capabilities_of_record,
    custom_endpoint,
    openrouter_endpoint,
    readiness,
    resolve_endpoint,
)
from swe_mux.openrouter import OpenRouterClient, apply_session_routing

from .test_openrouter_phase6 import FakeResponse, FakeSession, MemorySecrets

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _custom(model: str = "qwen2.5-coder:7b") -> Any:
    return custom_endpoint(base_url="http://127.0.0.1:11434/v1", model=model)


def _annotated(model: str = "") -> Any:
    return custom_endpoint(
        base_url="http://127.0.0.1:8190/openai/v1",
        model=model,
        capabilities=EndpointCapabilities(
            catalog="annotated", reports_cost=True, reports_cache=True
        ),
    )


# --- what is assumed when nothing was measured --------------------------------


def test_an_unprobed_endpoint_behaves_exactly_as_it_did_before_capabilities() -> None:
    endpoint = _custom()
    assert endpoint.capabilities == UNPROVEN_CAPABILITIES
    assert endpoint.supports_model_catalog is False
    assert endpoint.supports_provider_routing is False
    assert endpoint.supports_generation_cost is False
    assert endpoint.reports_cost is False
    assert endpoint.cache_policy == "unknown"
    assert endpoint.model_override == "qwen2.5-coder:7b"


def test_openrouter_is_known_rather_than_probed() -> None:
    # Spending a verify round-trip to rediscover the origin swe-mux ships against
    # would only add a way for the default provider to appear broken.
    endpoint = openrouter_endpoint()
    assert endpoint.capabilities == OPENROUTER_CAPABILITIES
    assert endpoint.reports_cost is True
    assert endpoint.supports_generation_cost is True


# --- what an annotated catalog unlocks ----------------------------------------


def test_an_annotated_catalog_is_what_unlocks_the_openrouter_behaviours() -> None:
    # One signal drives all three because all three answer the same question: is
    # there an OpenRouter on the other side of this URL.
    endpoint = _annotated(model="qwen2.5-coder:7b")
    assert endpoint.supports_model_catalog is True
    assert endpoint.supports_provider_routing is True
    assert endpoint.cache_policy == "by_model"
    assert endpoint.reports_cost is True
    # `/generation` stays OpenRouter's own accounting API regardless: a proxy that
    # reports cost in `usage` has no such route, and asking would 404 per call.
    assert endpoint.supports_generation_cost is False


def test_a_catalog_endpoint_stops_redirecting_every_callers_model() -> None:
    # The single-model redirect exists because there was nothing to choose from.
    # Once there is, each feature's own model setting means what it says.
    endpoint = _annotated(model="qwen2.5-coder:7b")
    assert endpoint.model_override == ""
    assert endpoint.pins_one_model is False
    assert endpoint.resolve_model("openai/gpt-5.6-terra") == "openai/gpt-5.6-terra"
    assert _custom().resolve_model("openai/gpt-5.6-terra") == "qwen2.5-coder:7b"
    assert _custom().pins_one_model is True


def test_a_bare_catalog_lists_models_without_claiming_to_price_or_route_them() -> None:
    # The middle outcome, and the reason this is not a boolean.
    endpoint = custom_endpoint(
        base_url="http://127.0.0.1:1234/v1",
        model="qwen2.5-coder:7b",
        capabilities=EndpointCapabilities(catalog="bare"),
    )
    assert endpoint.supports_model_catalog is False
    assert endpoint.supports_provider_routing is False
    assert endpoint.cache_policy == "unknown"
    assert endpoint.model_override == "qwen2.5-coder:7b"


def test_a_proven_catalog_endpoint_gets_sticky_routing_and_full_usage() -> None:
    # Gated on `supports_provider_routing` rather than on being OpenRouter itself.
    # Identity-gating cost a faithful proxy its prompt-cache affinity between
    # turns, whose measured shape is a ledger where every first call of a turn
    # reports zero cached tokens.
    body: dict[str, Any] = {}
    apply_session_routing(body, "conversation-7", endpoint=_annotated())
    assert body["session_id"] == "conversation-7"
    assert body["usage"] == {"include": True}
    # An unproven one still gets neither, so a strict local server sees no field
    # it never agreed to accept.
    bare: dict[str, Any] = {}
    apply_session_routing(bare, "conversation-7", endpoint=_custom())
    assert bare == {}


def test_a_blank_model_is_ready_with_a_catalog_and_not_without_one() -> None:
    proven = _annotated()
    verdict = readiness(proven, api_key="k", verified_fingerprint=proven.fingerprint("k"))
    assert verdict.ready is True
    blank = custom_endpoint(base_url="http://127.0.0.1:11434/v1", model="")
    assert readiness(blank, api_key=None, verified_fingerprint=None).code == "no_model"


# --- the probe ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], "none"),
        ([{"id": "qwen2.5-coder:7b"}, {"id": "llama3.1:8b"}], "bare"),
        (
            [
                {"id": "a", "supported_parameters": ["response_format"],
                 "pricing": {"prompt": "1"}},
                {"id": "b", "supported_parameters": ["tools"], "pricing": {"prompt": "2"}},
                {"id": "c"},
            ],
            "annotated",
        ),
        # One annotated row among bare ones does not make a catalog. This is the
        # direction that would hurt: it switches on routing directives and cache
        # markers against a server that serves neither.
        (
            [
                {"id": "a", "supported_parameters": ["response_format"],
                 "pricing": {"prompt": "1"}},
                {"id": "b"},
                {"id": "c"},
            ],
            "bare",
        ),
        # Present but empty is not annotation. OpenRouter-compatible shims often
        # emit the keys with nothing in them.
        (
            [{"id": "a", "supported_parameters": [], "pricing": {}}],
            "bare",
        ),
    ],
)
async def test_the_catalog_probe_classifies_by_what_the_entries_carry(
    entries: list[dict[str, Any]], expected: str
) -> None:
    session = FakeSession([FakeResponse(200, {"data": entries})])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    assert await client.probe_catalog() == expected


async def test_an_endpoint_that_refuses_models_is_the_ordinary_single_model_case() -> None:
    # Not an error worth surfacing: the verify completion running beside this is
    # what decides whether the endpoint works at all.
    session = FakeSession([FakeResponse(404, {"error": {"message": "nope"}})])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    assert await client.probe_catalog() == "none"


# --- storage and resolution ---------------------------------------------------


def test_a_record_written_before_capabilities_existed_reads_as_unproven() -> None:
    # The migration's whole contract. An old row proves the endpoint *works* and
    # says nothing about what it can do.
    assert capabilities_of_record({"fingerprint": "x"}) == UNPROVEN_CAPABILITIES
    assert capabilities_of_record({"capabilities_json": ""}) == UNPROVEN_CAPABILITIES
    assert capabilities_of_record({"capabilities_json": "not json"}) == UNPROVEN_CAPABILITIES
    assert capabilities_of_record(None) == UNPROVEN_CAPABILITIES
    assert capabilities_of_record(
        {"capabilities_json": json.dumps({"catalog": "annotated", "reports_cost": True})}
    ) == EndpointCapabilities(catalog="annotated", reports_cost=True)
    # An unknown catalog value is not trusted into the optimistic branch.
    assert (
        capabilities_of_record({"capabilities_json": json.dumps({"catalog": "wishful"})})
        == UNPROVEN_CAPABILITIES
    )


def test_the_capability_store_answers_a_miss_with_the_pessimistic_profile() -> None:
    store = CapabilityStore()
    assert store.get("custom") == UNPROVEN_CAPABILITIES
    store.set("custom", EndpointCapabilities(catalog="annotated"))
    assert store.get("custom").catalog == "annotated"
    store.clear("custom")
    assert store.get("custom") == UNPROVEN_CAPABILITIES


def test_clearing_the_store_outright_is_what_an_endpoint_edit_does() -> None:
    # The one way this feature could do harm rather than merely fail to help: a
    # base URL changed from an OpenRouter-shaped proxy to a local Ollama, keeping
    # its `annotated` record, would send a routing block and a cache breakpoint to
    # a server that has never heard of either.
    store = CapabilityStore()
    store.set("custom", EndpointCapabilities(catalog="annotated"))
    store.set("openrouter", EndpointCapabilities(catalog="annotated"))
    store.clear()
    assert store.get("custom") == UNPROVEN_CAPABILITIES
    assert store.get("openrouter") == UNPROVEN_CAPABILITIES


def test_resolving_reads_the_measurement_out_of_the_store() -> None:
    config = Config(
        llm_provider="custom",
        custom_llm_base_url="http://127.0.0.1:8190/openai/v1",
        custom_llm_model="qwen2.5-coder:7b",
    )
    store = CapabilityStore()
    assert resolve_endpoint(config, store).supports_model_catalog is False
    store.set("custom", EndpointCapabilities(catalog="annotated"))
    assert resolve_endpoint(config, store).supports_model_catalog is True
    # A record may be passed outright, which is what a test or a one-off wants.
    assert resolve_endpoint(
        config, EndpointCapabilities(catalog="annotated")
    ).supports_model_catalog is True
    # Omitting it is the unproven profile, which is what every caller had before.
    assert resolve_endpoint(config).supports_model_catalog is False


def test_a_measurement_never_reaches_the_default_provider() -> None:
    # OpenRouter's profile is known, so a stray `custom` record - or a hand-edited
    # one claiming anything at all - cannot alter how the default endpoint behaves.
    store = CapabilityStore()
    store.set("openrouter", EndpointCapabilities(catalog="none"))
    endpoint = resolve_endpoint(Config(llm_provider="openrouter"), store)
    assert endpoint.capabilities == OPENROUTER_CAPABILITIES
    assert endpoint.supports_model_catalog is True
