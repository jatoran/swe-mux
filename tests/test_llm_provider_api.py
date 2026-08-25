"""The provider status and verify endpoints, and what a Project's payload says.

The unit tests in `test_custom_llm_endpoint.py` hold the rules. These hold the
wire: that verifying writes a durable record and reports the endpoint's own words,
that a failure records nothing, that editing the endpoint afterwards un-verifies
it through the real HTTP path, and that a held-back automation arrives at the
browser carrying a reason rather than merely missing from `enabled`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.llm_endpoint import CapabilityStore
from swe_mux.openrouter import CatalogProbe, OpenRouterError, OpenRouterVerification
from swe_mux.project_files import read_project_config, write_project_config
from swe_mux.routes.automation import (
    automation_provider_key,
    automation_provider_status,
    get_project_automations,
    verify_automation_provider,
)
from swe_mux.server import error_middleware

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ProjectStub:
    def __init__(self, root: Path) -> None:
        self.id = "proj-1"
        self.name = "repo"
        self.root = str(root)


class ProjectsStub:
    def __init__(self, project: ProjectStub) -> None:
        self.projects = {project.id: project}

    def ordered_projects(self) -> list[ProjectStub]:
        return list(self.projects.values())


class SecretsStub:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, name: str) -> str | None:
        return self.values.get(name) or None

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def clear(self, name: str) -> None:
        self.values.pop(name, None)

    def status(self, name: str) -> dict[str, Any]:
        return {"configured": bool(self.values.get(name)), "source": "stub"}


class ProviderStub:
    """Scripted `verify`/`test_key`, recording the endpoint each was handed."""

    def __init__(self, *, output: str = "swe-mux endpoint check ok.",
                 error: str = "", catalog: str = "none",
                 catalog_error: str = "") -> None:
        self.output = output
        self.error = error
        self.catalog = catalog
        self.catalog_error = catalog_error
        self.verified: list[str] = []
        self.probed: list[str] = []
        self.listed: list[str] = []

    async def verify(self, *, endpoint: Any = None, model: str = "",
                     max_tokens: int = 32) -> OpenRouterVerification:
        self.verified.append(endpoint.provider)
        if self.error:
            raise OpenRouterError(self.error)
        effective = endpoint.resolve_model(model)
        if not effective:
            # Faithful to the real client, and load-bearing: the fallback this
            # replaced made a blank model verify happily in tests while raising in
            # production, which is the one asymmetry a stub must not have.
            raise OpenRouterError("an exact model id is required to verify an endpoint")
        return OpenRouterVerification(
            provider=endpoint.provider,
            origin=endpoint.origin,
            requested_model=effective,
            resolved_model="qwen2.5-coder:7b",
            output=self.output,
            latency_ms=91,
            input_tokens=11,
            output_tokens=7,
            cost_usd=None,
        )

    async def catalog_probe(self, *, endpoint: Any = None) -> CatalogProbe:
        self.probed.append(endpoint.provider)
        ids = ["qwen2.5-coder:7b"] if self.catalog != "none" else []
        return CatalogProbe(self.catalog, ids, self.catalog_error)

    async def models(self, *, endpoint: Any = None) -> list[dict[str, Any]]:
        self.listed.append(endpoint.provider)
        return [{"id": "qwen2.5-coder:7b", "name": "Qwen", "context_length": 8192,
                 "prompt_price": None, "completion_price": None,
                 "supported_parameters": ["response_format"]}]

    async def test_key(self, candidate: str | None = None, *, endpoint: Any = None
                       ) -> dict[str, Any]:
        return {"ok": True, "models": 1}


def build(tmp_path: Path, config: Config) -> tuple[web.Application, AutomationStore]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    store = AutomationStore(tmp_path / "automation.db")
    app = web.Application(middlewares=[error_middleware])
    app[keys.CONFIG] = config
    app[keys.PROJECTS] = ProjectsStub(ProjectStub(root))
    app[keys.EVENTS] = EventBus()
    app[keys.SECRET_STORE] = SecretsStub()
    app[keys.AUTOMATION_STORE] = store
    app[keys.OPENROUTER] = ProviderStub()
    # Starts empty, exactly as the daemon's does before anything is verified, so
    # these tests exercise the unproven path unless a verification fills it in.
    app[keys.LLM_CAPABILITIES] = CapabilityStore()
    app.router.add_get("/api/automation/provider", automation_provider_status)
    app.router.add_post("/api/automation/provider/key", automation_provider_key)
    app.router.add_post("/api/automation/provider/verify", verify_automation_provider)
    app.router.add_get("/api/projects/{project_id}/automations", get_project_automations)
    return app, store


def custom_config(tmp_path: Path, *, model: str = "qwen2.5-coder:7b",
                  base_url: str = "http://127.0.0.1:11434/v1") -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm_provider="custom",
        custom_llm_base_url=base_url,
        custom_llm_model=model,
    )


async def client_for(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_a_custom_endpoint_is_unverified_until_it_is_proven(tmp_path: Path) -> None:
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    client = await client_for(app)
    try:
        payload = await (await client.get("/api/automation/provider")).json()
        assert payload["provider"] == "custom"
        assert payload["llm"]["ready"] is False
        assert payload["llm"]["code"] == "unverified"
        # The sentence a surface renders verbatim, so it has to be one.
        assert "verify" in payload["llm"]["reason"].casefold()
        custom = next(item for item in payload["providers"] if item["id"] == "custom")
        assert custom["requires_verification"] is True
        assert custom["cache_policy"] == "unknown"
        # OpenRouter is listed too, so the panel can offer to prove either one before
        # switching the whole install onto it.
        assert {item["id"] for item in payload["providers"]} == {"openrouter", "custom"}

        result = await (await client.post("/api/automation/provider/verify", json={})).json()
        assert result["ok"] is True
        assert result["output"] == "swe-mux endpoint check ok."
        assert result["llm"]["ready"] is True
        assert result["verification"]["verified"] is True

        after = await (await client.get("/api/automation/provider")).json()
        assert after["llm"]["ready"] is True
        proven = next(item for item in after["providers"] if item["id"] == "custom")
        assert proven["verification"]["sample"] == "swe-mux endpoint check ok."
    finally:
        await client.close()
        store.close()


async def test_editing_the_endpoint_re_locks_it_with_a_stated_reason(tmp_path: Path) -> None:
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    client = await client_for(app)
    try:
        await client.post("/api/automation/provider/verify", json={})
        assert (await (await client.get("/api/automation/provider")).json())["llm"]["ready"]

        # The edit an operator actually makes: a different model on the same server.
        # Nothing clears the record; the fingerprint simply stops matching.
        config.custom_llm_model = "llama3.1:8b"
        payload = await (await client.get("/api/automation/provider")).json()
        assert payload["llm"]["ready"] is False
        assert payload["llm"]["code"] == "endpoint_changed"
        stale = next(item for item in payload["providers"] if item["id"] == "custom")
        # The record is kept and reported as stale: "you changed it" and "you never did
        # it" are different problems with different next steps.
        assert stale["verification"]["stale"] is True
        assert stale["verification"]["model"] == "qwen2.5-coder:7b"
    finally:
        await client.close()
        store.close()


async def test_a_failed_verification_does_not_disprove_the_last_good_one(
    tmp_path: Path,
) -> None:
    # An endpoint that worked yesterday and is unreachable this minute has not been
    # disproven. Deleting the record here would turn a network blip into a Project-wide
    # switch-off, which is the loud failure this feature exists to avoid causing.
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    client = await client_for(app)
    try:
        await client.post("/api/automation/provider/verify", json={})
        # Script the installed stub rather than replacing the app key: aiohttp
        # deprecates mutating application state once the app has started, and the
        # test only needs this call to fail, not a different provider object.
        installed = app[keys.OPENROUTER]
        assert isinstance(installed, ProviderStub)
        installed.error = "connection refused"
        response = await client.post("/api/automation/provider/verify", json={})
        assert response.status == 422
        body = await response.json()
        assert body["ok"] is False
        assert "connection refused" in body["error"]
        assert (await (await client.get("/api/automation/provider")).json())["llm"]["ready"]
    finally:
        await client.close()
        store.close()


async def test_replacing_the_key_drops_the_record_rather_than_leaving_a_stale_sample(
    tmp_path: Path,
) -> None:
    # The fingerprint covers the key, so a replacement un-verifies on its own. Dropping
    # the row as well keeps the surface from showing a reply a different credential
    # produced, which reads as reassurance for a state nobody proved.
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    client = await client_for(app)
    try:
        await client.post("/api/automation/provider/verify", json={})
        assert await store.provider_verification("custom") is not None
        await client.post(
            "/api/automation/provider/key",
            json={"operation": "set", "provider": "custom", "key": "rotated", "test": False},
        )
        assert await store.provider_verification("custom") is None
        payload = await (await client.get("/api/automation/provider")).json()
        assert payload["llm"]["code"] == "unverified"
    finally:
        await client.close()
        store.close()


async def test_an_unknown_provider_name_is_refused(tmp_path: Path) -> None:
    app, store = build(tmp_path, custom_config(tmp_path))
    client = await client_for(app)
    try:
        response = await client.post(
            "/api/automation/provider/verify", json={"provider": "anthropic-direct"}
        )
        assert response.status == 400
    finally:
        await client.close()
        store.close()


async def test_openrouter_is_ready_on_a_key_alone_so_no_install_regresses(
    tmp_path: Path,
) -> None:
    config = Config(data_dir=tmp_path / "data")
    app, store = build(tmp_path, config)
    app[keys.SECRET_STORE] = SecretsStub({"openrouter_api_key": "sk-or-v1-x"})
    client = await client_for(app)
    try:
        payload = await (await client.get("/api/automation/provider")).json()
        assert payload["provider"] == "openrouter"
        assert payload["llm"]["ready"] is True
        # Nothing was ever verified, and nothing needs to be: storing the key tested it.
        assert await store.provider_verification("openrouter") is None
    finally:
        await client.close()
        store.close()


async def test_a_held_back_automation_arrives_with_its_reason(tmp_path: Path) -> None:
    # The whole point of the section: an unverified provider must read as the stated
    # reason a switch is inert, never as a switch that is quietly missing from `enabled`.
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    root = Path(app[keys.PROJECTS].projects["proj-1"].root)
    current = await read_project_config(root)
    await write_project_config(
        root,
        {"automations": {"raw_store": True, "tier0": True, "scan_timeline": True,
                         "catch_me_up": True, "loop_detection": True}},
        current["revision"],
    )
    client = await client_for(app)
    try:
        payload = await (await client.get("/api/projects/proj-1/automations")).json()
        assert payload["requested"]["scan_timeline"] is True
        assert "scan_timeline" not in payload["enabled"]
        assert payload["unverified"] == ["scan_timeline"]
        assert payload["llm"]["ready"] is False
        assert payload["llm"]["reason"]
        # And the free consumers above it keep running on records that already exist.
        assert "catch_me_up" in payload["enabled"]
        assert "loop_detection" in payload["enabled"]
        # `needs_llm` travels on the registry so the browser reads one fact from one
        # source rather than keeping its own list of which automations call a model.
        registry = {item["id"]: item for item in payload["automations"]}
        assert registry["scan_timeline"]["needs_llm"] is True
        assert registry["loop_detection"]["needs_llm"] is False

        await client.post("/api/automation/provider/verify", json={})
        unlocked = await (await client.get("/api/projects/proj-1/automations")).json()
        assert "scan_timeline" in unlocked["enabled"]
        assert unlocked["unverified"] == []
    finally:
        await client.close()
        store.close()


async def test_verifying_measures_the_endpoint_and_the_measurement_takes_effect(
    tmp_path: Path,
) -> None:
    """The wire half of capabilities: probed, persisted, and live on the next call.

    The last clause is the one worth a test. Endpoint resolution is synchronous and
    per-request while the record lives in SQLite behind an async store, so nothing
    but a live capability store makes a verification land on the very next
    completion rather than on the next daemon restart - and a restart is exactly
    what the verify press is meant to make unnecessary.
    """
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "annotated"
    client = await client_for(app)
    try:
        before = await (await client.get("/api/automation/provider")).json()
        entry = next(item for item in before["providers"] if item["id"] == "custom")
        assert entry["verification"]["capabilities"]["catalog"] == "none"

        body = await (await client.post("/api/automation/provider/verify", json={})).json()
        assert body["ok"] is True
        assert body["capabilities"] == {
            "catalog": "annotated",
            "reports_cost": False,
            "reports_cache": False,
        }
        # Probed only after the completion proved the endpoint answers at all: a
        # catalog probe against an unreachable host reports `none`, and recording
        # that as a measurement would durably pin a capable endpoint to the
        # pessimistic profile over one bad minute.
        assert app[keys.OPENROUTER].probed == ["custom"]

        # Durable, and durable in the row rather than only in memory.
        record = await store.provider_verification("custom")
        assert record is not None
        assert json.loads(record["capabilities_json"])["catalog"] == "annotated"

        # And live: the store the per-request resolver reads was updated in place.
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "annotated"
        after = await (await client.get("/api/automation/provider")).json()
        proven = next(item for item in after["providers"] if item["id"] == "custom")
        assert proven["verification"]["capabilities"]["catalog"] == "annotated"
    finally:
        await client.close()
        store.close()


async def test_a_failed_verification_measures_nothing(tmp_path: Path) -> None:
    # Same reasoning as the record itself: an endpoint that is unreachable this
    # minute has not been disproven, and writing `catalog: none` for it would be a
    # durable downgrade earned by a network blip.
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "annotated"
    client = await client_for(app)
    try:
        await client.post("/api/automation/provider/verify", json={})
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "annotated"
        app[keys.OPENROUTER].error = "connection refused"
        app[keys.OPENROUTER].catalog = "none"
        response = await client.post("/api/automation/provider/verify", json={})
        assert response.status == 422
        # The probe runs first now, to answer which model the completion should go
        # to. What must still hold is that nothing it saw was *recorded*: a probe
        # against an unreachable host reports `none`, and durably pinning a capable
        # endpoint to the pessimistic profile over one bad minute is the failure.
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "annotated"
        record = await store.provider_verification("custom")
        assert record is not None
        assert json.loads(record["capabilities_json"])["catalog"] == "annotated"
    finally:
        await client.close()
        store.close()


async def test_the_probe_runs_against_the_endpoint_as_configured_not_as_measured(
    tmp_path: Path,
) -> None:
    """Re-verifying must not be circular, or an endpoint could never be downgraded.

    A previously-annotated endpoint carries a blank `model_override`, so resolving
    with the stored measurement would send the proving completion off with whatever
    model a feature happens to name. An endpoint edited down from a router to one
    local model would then verify against a model the new server has never heard
    of, and could never be re-proven.
    """
    config = custom_config(tmp_path, model="qwen2.5-coder:7b")
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "annotated"
    client = await client_for(app)
    try:
        await client.post("/api/automation/provider/verify", json={})
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "annotated"
        # Now it is a single-model server again.
        app[keys.OPENROUTER].catalog = "none"
        body = await (await client.post("/api/automation/provider/verify", json={})).json()
        assert body["ok"] is True
        assert body["requested_model"] == "qwen2.5-coder:7b"
        assert body["capabilities"]["catalog"] == "none"
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "none"
    finally:
        await client.close()
        store.close()


async def test_clearing_the_key_forgets_the_measurement_with_the_proof(
    tmp_path: Path,
) -> None:
    # Leaving an `annotated` record behind a cleared credential would keep sending
    # routing directives and cache markers on behalf of an endpoint nobody can now
    # reach, and would report a picker for a catalog nothing can fetch.
    config = custom_config(tmp_path)
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "annotated"
    client = await client_for(app)
    try:
        await client.post("/api/automation/provider/verify", json={})
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "annotated"
        await client.post("/api/automation/provider/key", json={"operation": "clear"})
        assert app[keys.LLM_CAPABILITIES].get("custom").catalog == "none"
        assert await store.provider_verification("custom") is None
    finally:
        await client.close()
        store.close()


async def test_a_catalog_endpoint_verifies_without_a_model_being_typed_in(
    tmp_path: Path,
) -> None:
    """The bootstrap, and the dead end it closes.

    An endpoint that publishes a catalog does not need its single-model field
    filled in - that is the whole point of measuring one. But *verifying* means
    sending one completion somewhere, so requiring a model in order to prove the
    field was unnecessary made the field block its own removal: point at a
    gateway, leave the model blank because blank is correct, and the panel says
    "no model id yet" on a screen that never mentions verifying.

    So the probe runs first and the completion goes to a model out of the catalog.
    """
    config = custom_config(tmp_path, model="")
    config.openrouter_cheap_model = "qwen2.5-coder:7b"
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "annotated"
    client = await client_for(app)
    try:
        before = await (await client.get("/api/automation/provider")).json()
        # Told to verify, not told to go and type an id.
        assert before["llm"]["code"] == "unverified"

        body = await (await client.post("/api/automation/provider/verify", json={})).json()
        assert body["ok"] is True
        # The install's own cheap model, because a reader already chose it for
        # high-volume work: a failure against it is informative rather than
        # incidental.
        assert body["requested_model"] == "qwen2.5-coder:7b"
        assert body["capabilities"]["catalog"] == "annotated"

        after = await (await client.get("/api/automation/provider")).json()
        assert after["llm"]["ready"] is True
    finally:
        await client.close()
        store.close()


async def test_a_catalog_less_endpoint_still_needs_its_one_model_named(
    tmp_path: Path,
) -> None:
    # The other side. With nothing to pick from there is no model to send the
    # proving completion to, and `verify` refuses with its own message rather than
    # this guessing at one.
    config = custom_config(tmp_path, model="")
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "none"
    client = await client_for(app)
    try:
        response = await client.post("/api/automation/provider/verify", json={})
        assert response.status == 422
        assert "publishes no model catalog" in (await response.json())["error"]
    finally:
        await client.close()
        store.close()


async def test_a_refused_catalog_is_not_reported_as_a_missing_model(
    tmp_path: Path,
) -> None:
    """The two states under one empty catalog, and why they cannot share a message.

    An endpoint whose catalog fetch is *refused* - the common case being one that
    wants a credential nobody has stored - looks identical from outside to one
    that genuinely publishes none. Reporting the first as "an exact model id is
    required" points at the single field that is correctly blank, and says nothing
    about the key or the URL that actually needs attention. Measured on the real
    install: a 401 from the gateway's catalog route surfaced exactly that way.
    """
    config = custom_config(tmp_path, model="")
    app, store = build(tmp_path, config)
    app[keys.OPENROUTER].catalog = "none"
    app[keys.OPENROUTER].catalog_error = "request failed with HTTP 401"
    client = await client_for(app)
    try:
        response = await client.post("/api/automation/provider/verify", json={})
        assert response.status == 422
        error = (await response.json())["error"]
        assert "HTTP 401" in error
        # It names the URL it could not read and the two controls that fix it.
        assert "/models" in error
        assert "API key" in error
        assert "model id is required" not in error
    finally:
        await client.close()
        store.close()
