"""Bring-your-own LLM endpoint, and gating on a verified provider (Phase 15).

Four things are worth holding here, and each one is a way the feature could have
looked fine and been wrong:

- The endpoint is *substituted*, not added alongside. A custom endpoint must not
  receive OpenRouter's routing directives, must not be asked for an OpenRouter
  accounting record, and must have its single model reach every caller that named
  an OpenRouter id.
- Editing the endpoint un-verifies it, and does so from the *data* rather than
  from a write path remembering to. The invariant is asserted against each of the
  three fields separately, because a fingerprint over two of them would pass a
  test that only changed the third.
- The caching assumption does not silently carry over. `anthropic/...` served by a
  local proxy must not receive a `cache_control` breakpoint, which is the one
  failure mode that would look like nothing at all until a provider rejected it.
- An unverified provider makes exactly the model-backed automations inert, and
  leaves the free consumers above them running.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swe_mux.automation_registry import REGISTRY, llm_dependent_ids, needs_llm, resolve
from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config, _validate
from swe_mux.grants import plan_grant
from swe_mux.llm_endpoint import (
    EndpointCapabilities,
    LlmEndpoint,
    base_url_error,
    custom_endpoint,
    model_error,
    normalize_base_url,
    openrouter_endpoint,
    readiness,
    resolve_endpoint,
    verification_state,
)
from swe_mux.openrouter import (
    VERIFY_MAX_TOKENS,
    OpenRouterClient,
    OpenRouterError,
    cache_stable_message,
    marks_cache_breakpoints,
)

from .test_openrouter_phase6 import FakeResponse, FakeSession, MemorySecrets


def _completion(
    content: str = "swe-mux endpoint check ok.", completion_tokens: int = 7
) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": "qwen2.5-coder:7b",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": completion_tokens},
    }


def _custom(model: str = "qwen2.5-coder:7b") -> LlmEndpoint:
    return custom_endpoint(base_url="http://127.0.0.1:11434/v1", model=model)


# --- the endpoint descriptor -------------------------------------------------


def test_a_base_url_is_checked_for_shape_and_not_for_where_it_points() -> None:
    # Permissive about the host on purpose: a LAN vLLM box and a hosted proxy are
    # both legitimate, and an allowlist would only stop the people this exists for.
    assert base_url_error("http://127.0.0.1:11434/v1") is None
    assert base_url_error("https://llm.internal.example.com:8443/v1") is None
    assert base_url_error("http://192.168.1.40:8000/v1") is None
    # Every rejected shape is one that fails confusingly rather than loudly.
    assert base_url_error("") is not None
    assert base_url_error("127.0.0.1:11434") is not None
    assert base_url_error("ftp://host/v1") is not None
    assert base_url_error("file:///etc/passwd") is not None
    assert "credentials" in (base_url_error("http://user:pass@host/v1") or "")
    assert base_url_error("http://host/v1?key=secret") is not None
    assert base_url_error("http://host:not-a-port/v1") is not None
    assert base_url_error("http://host/" + "x" * 400) is not None


def test_a_trailing_slash_is_trimmed_so_paths_append_cleanly() -> None:
    assert normalize_base_url("http://host/v1/") == "http://host/v1"
    assert normalize_base_url("  http://host/v1//  ") == "http://host/v1"


def test_a_blank_model_is_answered_by_readiness_rather_than_by_validation() -> None:
    # Whether a blank model can be tolerated depends on whether the endpoint serves a
    # catalog, which is a measured property in a SQLite row that `_validate` cannot
    # reach - so refusing blank here hardcoded "no catalog" for every endpoint. The
    # requirement did not disappear; it moved to `readiness`, which can see the answer.
    assert model_error("") is None
    assert model_error("   ") is None
    assert model_error("has space") is not None
    assert model_error("qwen2.5-coder:7b") is None


def test_a_single_model_endpoint_still_has_to_name_its_model() -> None:
    # The other half of the move above. Nothing was relaxed: an endpoint with no
    # catalog to pick from still cannot run, and now says so in a sentence rather
    # than as a form error on a field whose necessity depends on a probe.
    #
    # Asked of a *proven* endpoint, because that is the only state in which the
    # answer is knowable - see the ordering test below.
    endpoint = custom_endpoint(base_url="http://127.0.0.1:11434/v1", model="")
    blank = readiness(
        endpoint, api_key=None, verified_fingerprint=endpoint.fingerprint(None)
    )
    assert blank.code == "no_model"


def test_a_catalog_endpoint_needs_no_pinned_model_because_it_has_a_picker() -> None:
    # The reason the pin stopped being unconditional. An OpenRouter-shaped proxy has
    # something to choose between, so each feature's own model setting means what it
    # says instead of all of them collapsing onto one id.
    endpoint = custom_endpoint(
        base_url="http://127.0.0.1:8190/openai/v1",
        model="",
        capabilities=EndpointCapabilities(catalog="annotated", reports_cost=True),
    )
    verdict = readiness(
        endpoint, api_key="k", verified_fingerprint=endpoint.fingerprint("k")
    )
    assert verdict.ready
    assert endpoint.model_override == ""
    # And a caller's own model id survives to the wire rather than being redirected.
    assert endpoint.resolve_model("openai/gpt-5.6-terra") == "openai/gpt-5.6-terra"


def _errors(config: Config) -> dict[str, str]:
    try:
        _validate(config)
    except ValueError as exc:
        return dict(exc.args[0])
    return {}


def test_the_config_only_validates_the_endpoint_it_is_actually_using() -> None:
    # A half-filled custom endpoint left behind after switching back to OpenRouter is
    # inert configuration; refusing the whole settings form over it would make
    # switching *away* from a broken endpoint impossible.
    parked = Config(llm_provider="openrouter", custom_llm_base_url="nonsense", custom_llm_model="")
    assert not {"custom_llm_base_url", "custom_llm_model"} & set(_errors(parked))
    selected = Config(llm_provider="custom", custom_llm_base_url="nonsense", custom_llm_model="")
    errors = _errors(selected)
    assert "custom_llm_base_url" in errors
    # A blank model is no longer a form error - `readiness` states it instead, because
    # whether it is required depends on a catalog probe this validator cannot run.
    # A malformed one still is, since that is answerable from the value alone.
    assert "custom_llm_model" not in errors
    malformed = Config(
        llm_provider="custom",
        custom_llm_base_url="http://127.0.0.1:11434/v1",
        custom_llm_model="has space",
    )
    assert "custom_llm_model" in _errors(malformed)
    assert "llm_provider" in _errors(Config(llm_provider="anthropic-direct"))


def test_an_unrecognised_provider_falls_back_rather_than_refusing_to_start() -> None:
    # `validate_config` refuses to save one, so reaching here means a hand-edited file;
    # the honest recovery is the default everyone else has.
    assert resolve_endpoint(Config(llm_provider="nonsense")).provider == "openrouter"
    assert resolve_endpoint(Config()).origin == "https://openrouter.ai/api/v1"


# --- substitution, not addition ----------------------------------------------


async def test_a_custom_endpoint_gets_no_openrouter_routing_directives() -> None:
    session = FakeSession([FakeResponse(200, _completion())])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    await client.complete_tools(model="openai/gpt-5.6-terra", messages=[], tools=[], max_tokens=64)
    _method, url, kwargs = session.requests[0]
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    # `provider` is OpenRouter's vocabulary for choosing between hosts of one model.
    # A single-origin server has no hosts to choose between and never made the promise.
    assert "provider" not in kwargs["json"]


async def test_the_endpoints_own_model_replaces_every_callers_openrouter_id() -> None:
    # Every model setting in the app names an OpenRouter route a local server has never
    # heard of, so redirecting at the seam is what lets the assistant, the scan timeline
    # and the titler all work without any of them learning about providers.
    session = FakeSession([FakeResponse(200, _completion()), FakeResponse(200, _completion())])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    await client.complete_tools(model="anthropic/claude-sonnet-4.5", messages=[], tools=[],
                                max_tokens=64)
    assert session.requests[0][2]["json"]["model"] == "qwen2.5-coder:7b"
    # And a feature whose own model slot is blank still runs, rather than tripping the
    # "an exact model id is required" guard the OpenRouter path keeps.
    turn = await client.complete_tools(model="", messages=[], tools=[], max_tokens=64)
    assert session.requests[1][2]["json"]["model"] == "qwen2.5-coder:7b"
    assert turn.requested_model == "qwen2.5-coder:7b"


async def test_a_custom_endpoint_with_no_key_sends_no_authorization_header() -> None:
    # llama.cpp and Ollama serve unauthenticated. Demanding a placeholder token would
    # make the commonest local setup fail with a message about a credential nobody wants.
    session = FakeSession([FakeResponse(200, _completion())])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    await client.verify()
    assert "Authorization" not in session.requests[0][2]["headers"]


async def test_openrouter_still_refuses_without_a_key() -> None:
    client = OpenRouterClient(MemorySecrets(""), session=FakeSession([]))
    with pytest.raises(OpenRouterError, match="OpenRouter key is not configured"):
        await client.test_key()


async def test_a_custom_endpoints_cost_is_unknown_rather_than_zero() -> None:
    # `/generation` is an OpenRouter accounting API, not part of the OpenAI-compatible
    # surface. `None` already means "unknown cost" to every caller, which is the truthful
    # answer for a local server and for a proxy that bills without saying so alike.
    session = FakeSession([])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    assert await client.generation_cost("gen-1") is None
    assert session.requests == []


async def test_a_bare_models_list_is_kept_rather_than_filtered_to_nothing() -> None:
    # An OpenAI-compatible `/models` carries no `supported_parameters`, so OpenRouter's
    # capability filter would drop every entry and report a server with a loaded model as
    # having none - "advertises nothing" read as "does not support response_format".
    session = FakeSession([FakeResponse(200, {"data": [{"id": "qwen2.5-coder:7b"}]})])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    models = await client.models()
    assert [item["id"] for item in models] == ["qwen2.5-coder:7b"]


async def test_a_failing_custom_endpoint_does_not_blame_openrouter() -> None:
    # The diagnostic that matters most: an operator told "OpenRouter request failed"
    # about their own llama.cpp goes and checks the wrong thing entirely.
    session = FakeSession([FakeResponse(404, {"error": {"message": "model not found"}})])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    with pytest.raises(OpenRouterError) as caught:
        await client.verify()
    assert "OpenRouter" not in str(caught.value)


# --- caching must not silently assume ----------------------------------------


def test_a_custom_endpoint_never_receives_a_cache_breakpoint() -> None:
    # The one that would fail silently. Marking is portable because OpenRouter
    # translates it; a local proxy has nothing doing that, and may legitimately
    # serve a model called `anthropic/claude-sonnet-4.5` while having never heard
    # of `cache_control`.
    assert marks_cache_breakpoints("anthropic/claude-sonnet-4.5")
    assert not marks_cache_breakpoints("anthropic/claude-sonnet-4.5", cache_policy="unknown")
    message = {"role": "system", "content": "primer"}
    assert cache_stable_message(
        message, model="anthropic/claude-sonnet-4.5", cache_policy="unknown"
    ) is message


def test_the_implicit_cache_assumption_is_marked_unknown_rather_than_inherited() -> None:
    # The other half, and the half that has no request to reject it: "everything I do not
    # recognise caches implicitly" is a safe default for OpenRouter routes and a false
    # claim about a local server, so a custom endpoint declares neither.
    assert openrouter_endpoint().cache_policy == "by_model"
    assert _custom().cache_policy == "unknown"


def test_every_openrouter_route_keeps_its_breakpoint() -> None:
    for model in ("anthropic/claude-sonnet-4.5", "openai/gpt-5.6-terra", "z-ai/glm-5"):
        marked = cache_stable_message({"role": "system", "content": "primer"}, model=model)
        assert marked["content"][-1]["cache_control"] == {"type": "ephemeral"}


# --- verification, and un-verifying on edit ----------------------------------


async def test_a_verification_returns_the_endpoints_own_words() -> None:
    # "Reachable" and "usable" are different findings and only the text separates them:
    # an empty reply or a chat template's own scaffolding passes every check a boolean
    # could make.
    session = FakeSession([FakeResponse(200, _completion("swe-mux endpoint check ok."))])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    result = await client.verify()
    assert result.output == "swe-mux endpoint check ok."
    assert result.requested_model == "qwen2.5-coder:7b"
    body = session.requests[0][2]["json"]
    # Deliberately the plain shape: a verify that also demanded structured output would
    # fail on endpoints that work perfectly well, and a verify that could fail for two
    # reasons is not a verify.
    assert "response_format" not in body
    assert "tools" not in body
    assert body["max_tokens"] == VERIFY_MAX_TOKENS


async def test_a_reasoning_model_that_thinks_past_the_probe_is_not_called_broken() -> None:
    # Measured against the real gateway: `openai/gpt-5-nano` spent the whole 32-token
    # budget reasoning and returned an empty string, which the verify action reports as
    # the one finding it exists to catch. Reasoning draws from the same budget, so the
    # empty reply said nothing about the endpoint and everything about the question.
    session = FakeSession([FakeResponse(200, _completion("", completion_tokens=64))])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    result = await client.verify()
    assert result.output == ""
    assert result.spent_budget_reasoning is True


async def test_an_endpoint_that_truly_answers_with_nothing_is_still_reported() -> None:
    # The other side of the same discrimination, and the reason it is a token count
    # rather than a blanket exemption: no output tokens billed means the endpoint
    # really did answer with nothing, which is the finding a verify must not soften.
    session = FakeSession([FakeResponse(200, _completion("", completion_tokens=0))])
    client = OpenRouterClient(MemorySecrets(""), session=session, endpoint=_custom())
    result = await client.verify()
    assert result.output == ""
    assert result.spent_budget_reasoning is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://127.0.0.1:8080/v1"),
        ("model", "llama3.1:8b"),
        ("api_key", "rotated"),
    ],
)
def test_editing_any_part_of_the_endpoint_un_verifies_it(field: str, value: str) -> None:
    # The invariant, asserted per field rather than once: a fingerprint covering two of
    # the three would pass a single-case test while leaving the third silently trusted.
    # A rotated key is as capable of turning a working endpoint into a 401 as a bad URL.
    original = _custom()
    record = {"fingerprint": original.fingerprint("original-key")}
    assert verification_state(original, api_key="original-key", record=record)["verified"]

    edited = (
        custom_endpoint(base_url=value, model=original.model_override)
        if field == "base_url"
        else custom_endpoint(base_url=original.origin, model=value)
        if field == "model"
        else original
    )
    key = value if field == "api_key" else "original-key"
    state = verification_state(edited, api_key=key, record=record)
    assert not state["verified"]
    # "You changed it" and "you never did it" are different problems, so the row is kept
    # and reported as stale rather than silently discarded.
    assert state["stale"]


def test_an_endpoint_with_no_record_reads_as_never_verified_not_as_edited() -> None:
    state = verification_state(_custom(), api_key=None, record=None)
    assert not state["verified"]
    assert not state["stale"]


def test_readiness_names_the_specific_thing_to_fix() -> None:
    # Four not-ready states need four different next actions, so they are four codes and
    # four sentences rather than one "not configured".
    assert readiness(openrouter_endpoint(), api_key=None,
                     verified_fingerprint=None).code == "no_key"
    assert readiness(openrouter_endpoint(), api_key="sk-or-x",
                     verified_fingerprint=None).ready
    blank = custom_endpoint(base_url="", model="")
    assert readiness(blank, api_key=None, verified_fingerprint=None).code == "no_endpoint"
    # `no_model` only after the endpoint is proven: until then nobody knows whether
    # it publishes a catalog, so nobody knows whether a model id is needed at all.
    no_model = custom_endpoint(base_url="http://host/v1", model="")
    assert readiness(no_model, api_key=None, verified_fingerprint=None).code == "unverified"
    assert readiness(
        no_model, api_key=None, verified_fingerprint=no_model.fingerprint(None)
    ).code == "no_model"
    endpoint = _custom()
    assert readiness(endpoint, api_key=None,
                     verified_fingerprint=None).code == "unverified"
    assert readiness(endpoint, api_key=None,
                     verified_fingerprint="stale-digest").code == "endpoint_changed"
    assert readiness(endpoint, api_key=None,
                     verified_fingerprint=endpoint.fingerprint(None)).ready


def test_openrouter_needs_no_separate_verification_so_no_install_regresses() -> None:
    # Storing an OpenRouter key already tests it against an origin swe-mux ships, so
    # configuring it *is* verifying it. Requiring a second act here would switch off
    # every existing install's model-backed automations on upgrade.
    assert not openrouter_endpoint().requires_verification
    assert _custom().requires_verification
    every_reason = readiness(openrouter_endpoint(), api_key="sk-or-x", verified_fingerprint=None)
    assert every_reason.ready


def test_the_fingerprint_reveals_nothing_key_shaped() -> None:
    endpoint = _custom()
    digest = endpoint.fingerprint("sk-super-secret-value")
    assert "sk-super-secret-value" not in digest
    assert len(digest) == 64


async def test_the_verification_record_survives_a_restart(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.db")
    endpoint = _custom()
    await store.record_provider_verification(
        provider="custom",
        fingerprint=endpoint.fingerprint("k"),
        base_url=endpoint.origin,
        model=endpoint.model_override,
        resolved_model="qwen2.5-coder:7b",
        sample="swe-mux endpoint check ok.",
        latency_ms=142,
    )
    store.close()

    reopened = AutomationStore(tmp_path / "automation.db")
    record = await reopened.provider_verification("custom")
    assert record is not None
    assert verification_state(endpoint, api_key="k", record=record)["verified"]
    assert record["sample"] == "swe-mux endpoint check ok."
    await reopened.clear_provider_verification("custom")
    assert await reopened.provider_verification("custom") is None
    reopened.close()


# --- gating through the existing dependency graph ----------------------------


def test_only_the_model_backed_automations_go_inert() -> None:
    # Subtracting from `enabled` rather than from `requested` is the whole design: the
    # free consumers layered over the timeline read records that already exist, and
    # switching them off because somebody rotated a key would be a second failure.
    requested = {"raw_store", "tier0", "scan_timeline", "catch_me_up", "live_blockers",
                 "loop_detection"}
    ready = resolve(requested, llm_ready=True)
    assert "scan_timeline" in ready.enabled
    assert ready.unverified == frozenset()

    held = resolve(requested, llm_ready=False)
    assert "scan_timeline" not in held.enabled
    assert held.unverified == frozenset({"scan_timeline"})
    for free in ("catch_me_up", "live_blockers", "loop_detection", "tier0", "raw_store"):
        assert free in held.enabled, f"{free} calls no model and must keep running"


def test_unverified_is_its_own_field_and_never_a_blocked_entry() -> None:
    # `blocked` values are automation ids a grant can switch on. No automation's enabling
    # fixes an unverified endpoint, so merging the two would render a gate offering to
    # turn on nothing.
    held = resolve({"raw_store", "tier0", "scan_timeline"}, llm_ready=False)
    assert held.blocked == {}
    assert held.unverified == frozenset({"scan_timeline"})


def test_a_blocked_automation_is_not_also_reported_as_unverified() -> None:
    # It is held back by a dependency it can be granted, which is the actionable answer;
    # reporting both would present two different fixes for one switch.
    held = resolve({"scan_timeline"}, llm_ready=False)
    assert held.blocked["scan_timeline"] == ("raw_store", "tier0")
    assert held.unverified == frozenset()


def test_resolution_defaults_to_pure_dag_so_a_caller_with_no_provider_is_unchanged() -> None:
    assert resolve({"raw_store", "tier0", "scan_timeline"}).unverified == frozenset()


def test_needing_a_model_is_asked_of_the_closure() -> None:
    # `catch_me_up` calls nothing and cannot be switched on without `scan_timeline`,
    # which does - the same rule `spends_money` follows.
    assert needs_llm(["catch_me_up"])
    assert not needs_llm(["loop_detection"])
    assert llm_dependent_ids() == frozenset(
        {"scan_timeline", "continuous_title", "model_narration"}
    )


def test_every_spending_automation_needs_the_provider() -> None:
    # Every way of spending money here is a model call, so an automation that claimed to
    # spend and denied needing the provider would bill from outside the gate. Enforced at
    # import in `_validate_registry`; asserted here so the reason is written down.
    for automation in REGISTRY.values():
        assert not automation.spends or automation.needs_llm, automation.id


def test_a_grant_discloses_the_provider_requirement_before_the_press() -> None:
    # The grant still lands - the opt-in is a real permission and withholding it would
    # mean granting twice - so the gate has to say the switch will be inert rather than
    # hand back an enabled-and-does-nothing state.
    plan = plan_grant(
        install={}, automations=["catch_me_up"], values=None,
        current_install={}, current_automations={}, current_values={},
    )
    assert plan.needs_llm
    assert plan.spends
    free = plan_grant(
        install={}, automations=["loop_detection"], values=None,
        current_install={}, current_automations={}, current_values={},
    )
    assert not free.needs_llm
    assert not free.spends


def test_turning_on_a_model_backed_install_switch_discloses_it_too() -> None:
    plan = plan_grant(
        install={"scan_timeline_enabled": True}, automations=None, values=None,
        current_install={}, current_automations={}, current_values={},
    )
    assert plan.needs_llm
    quiet = plan_grant(
        install={"clipboard_history_enabled": True}, automations=None, values=None,
        current_install={}, current_automations={}, current_values={},
    )
    assert not quiet.needs_llm


def test_the_verify_payload_shape_is_stable_enough_to_render() -> None:
    # The browser branches on `code` and renders `reason` verbatim, so both travel.
    # `reports_cost` travels with them because it is a property of the endpoint that
    # only the daemon knows, and the budget controls need it to say that a dollar cap
    # cannot bind against a provider reporting no cost (`src/swe_mux/budget.py`).
    payload = readiness(_custom(), api_key=None, verified_fingerprint=None).as_dict()
    assert set(payload) == {"ready", "provider", "code", "reason", "reports_cost"}
    assert payload["reports_cost"] is False
    assert json.dumps(payload)


def test_readiness_and_cost_reporting_are_independent_facts() -> None:
    """An unproven endpoint may still be one that prices, and a ready one may not be.

    Deriving the budget warning from `ready` would silence it on exactly the install
    it exists for - a local endpoint the operator has just verified.
    """
    proven = readiness(
        _custom(), api_key=None, verified_fingerprint=_custom().fingerprint(None)
    )
    assert proven.ready is True
    assert proven.reports_cost is False
