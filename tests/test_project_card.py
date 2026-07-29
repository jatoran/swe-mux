"""Control-plane project card (CP §5.4, roadmap Phase 5.5).

Sits alongside `test_control_plane_enablement.py` (the substrate's opt-in gate)
and `test_deterministic_consumers.py` (the model-free detectors). What is being
pinned here is mostly *refusal*: the card must be absent rather than wrong when
a provider is missing, a budget is spent, or its sources changed under it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from swe_mux import automation_registry as registry
from swe_mux.automation_store import AutomationStore
from swe_mux.openrouter import OpenRouterError, OpenRouterResult
from swe_mux.project_card import (
    PROJECT_CARD_RULE_ID,
    CardSources,
    ProjectCard,
    ProjectCardContext,
    ProjectCardService,
    Subsystem,
    card_from_snapshot,
    gather_sources,
)


@dataclass
class FakeConfig:
    automation_enabled: bool = True
    openrouter_cheap_model: str = "cheap/model"
    project_card_model: str = ""
    project_card_daily_budget_usd: float = 0.25
    project_card_max_input_tokens: int = 6000
    project_card_max_output_tokens: int = 600


class FakeProvider:
    """Records every call so "built once" can be asserted rather than assumed."""

    def __init__(self, value: dict[str, Any] | None = None) -> None:
        self.value = value or {
            "summary": "swe-mux multiplexes agent CLI sessions behind one daemon.",
            "subsystems": [
                {"name": "daemon", "purpose": "owns sessions and the HTTP surface"},
                {"name": "adapters", "purpose": "normalizes Claude and Codex evidence"},
            ],
        }
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return OpenRouterResult(
            generation_id="gen-1",
            requested_model=str(kwargs["model"]),
            resolved_model=str(kwargs["model"]),
            value=self.value,
            input_tokens=1200,
            output_tokens=180,
            cost_usd=0.002,
            latency_ms=400,
        )


@pytest.fixture
def store_path() -> Path:
    path = Path(__file__).parent / f".cards-{uuid.uuid4().hex}.db"
    yield path
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def _project(root: Path, *, overview: str = "swe-mux is a session multiplexer.") -> None:
    docs = root / ".docs"
    (docs / "design" / "features").mkdir(parents=True, exist_ok=True)
    (docs / "00_OVERVIEW.md").write_text(f"# Overview\n\n{overview}\n", encoding="utf-8")
    (docs / "CLAUDE.md").write_text(
        "# Documentation routing\n\n- Changing sessions: `design/features/sessions.md`\n",
        encoding="utf-8",
    )
    (docs / "design" / "features" / "sessions.md").write_text(
        "# Sessions\n\n## Key files\n\n- `src/swe_mux/session.py` — session manager\n"
        "- `src/swe_mux/pty_host.py` — PTY host\n",
        encoding="utf-8",
    )
    (docs / "design" / "features" / "ui.md").write_text(
        "# UI\n\n## Key files\n\n- `frontend/src/App.tsx` — shell\n",
        encoding="utf-8",
    )


def _service(
    store: AutomationStore,
    root: Path,
    provider: FakeProvider,
    *,
    config: FakeConfig | None = None,
    enabled: bool = True,
) -> ProjectCardService:
    async def resolve_session(session_id: str) -> ProjectCardContext | None:
        if not enabled or session_id != "s1":
            return None
        return ProjectCardContext(project_id="p1", project_root=str(root))

    async def resolve_project(project_root: str) -> bool:
        del project_root
        return enabled

    return ProjectCardService(
        store,
        config or FakeConfig(),
        provider,
        resolve_session=resolve_session,
        resolve_project=resolve_project,
    )


# ---- Sources and the invalidation rule ---------------------------------------


def test_sources_invert_key_files_into_areas(tmp_path: Path) -> None:
    _project(tmp_path)
    sources = gather_sources(str(tmp_path), model="cheap/model")
    assert sources.overview_source == ".docs/00_OVERVIEW.md"
    areas = dict(sources.areas)
    assert areas["design/features/sessions.md"] == (
        "src/swe_mux/pty_host.py",
        "src/swe_mux/session.py",
    )
    assert areas["design/features/ui.md"] == ("frontend/src/app.tsx",)
    assert sources.usable


def test_fingerprint_changes_with_every_input_that_shapes_the_card(tmp_path: Path) -> None:
    """The stated invalidation rule: sources, model, prompt and schema version."""
    _project(tmp_path)
    base = gather_sources(str(tmp_path), model="cheap/model").fingerprint
    assert gather_sources(str(tmp_path), model="cheap/model").fingerprint == base
    # A different model is a different card.
    assert gather_sources(str(tmp_path), model="other/model").fingerprint != base
    # An edited overview is a different card.
    _project(tmp_path, overview="swe-mux is now something else entirely.")
    assert gather_sources(str(tmp_path), model="cheap/model").fingerprint != base
    # So is a doc adopting a new key file.
    _project(tmp_path, overview="swe-mux is now something else entirely.")
    changed = gather_sources(str(tmp_path), model="cheap/model").fingerprint
    (tmp_path / ".docs" / "design" / "features" / "sessions.md").write_text(
        "# Sessions\n\n## Key files\n\n- `src/swe_mux/session.py` — session manager\n"
        "- `src/swe_mux/lifecycle.py` — lifecycle\n",
        encoding="utf-8",
    )
    assert gather_sources(str(tmp_path), model="cheap/model").fingerprint != changed


def test_a_project_with_no_documentation_yields_no_sources(tmp_path: Path) -> None:
    sources = gather_sources(str(tmp_path), model="cheap/model")
    assert not sources.usable
    assert sources.areas == ()


# ---- Rendering ---------------------------------------------------------------


def _card(areas: tuple[tuple[str, tuple[str, ...]], ...]) -> ProjectCard:
    return ProjectCard(
        project_id="p1",
        summary="A multiplexer.",
        subsystems=(Subsystem("daemon", "owns sessions"),),
        areas=areas,
        fingerprint="fp",
        overview_source=".docs/00_OVERVIEW.md",
        model="cheap/model",
        built_at=1.0,
    )


def test_render_is_compact_and_names_areas() -> None:
    card = _card((("design/features/sessions.md", ("src/swe_mux/session.py",)),))
    rendered = card.render()
    assert "# Project card" in rendered
    assert "daemon: owns sessions" in rendered
    assert "design/features/sessions.md: src/swe_mux/session.py" in rendered
    # A few hundred tokens is the design bound; ~4 chars/token.
    assert len(rendered) < 4000


def test_render_states_what_it_dropped() -> None:
    """A truncated map that looks complete reads as "nothing else is documented"."""
    areas = tuple(
        (f"design/features/doc{index}.md", tuple(f"src/f{index}_{n}.py" for n in range(10)))
        for index in range(20)
    )
    rendered = _card(areas).render(max_files=20, max_areas=5)
    assert "more file(s) across" in rendered
    assert "not shown" in rendered


def test_areas_for_answers_the_inverse_lookup() -> None:
    card = _card(
        (
            ("design/features/sessions.md", ("src/swe_mux/session.py",)),
            ("design/features/ui.md", ("frontend/src/app.tsx",)),
        )
    )
    assert card.areas_for("src/swe_mux/session.py") == ("design/features/sessions.md",)
    # Absolute paths under the root normalize to the same entry.
    assert card.areas_for("C:/repo/src/swe_mux/session.py", "C:/repo") == (
        "design/features/sessions.md",
    )
    assert card.areas_for("src/swe_mux/nothing.py") == ()


def test_card_round_trips_through_its_snapshot() -> None:
    card = _card((("design/features/sessions.md", ("src/swe_mux/session.py",)),))
    restored = card_from_snapshot("p1", json.loads(json.dumps(card.snapshot())))
    assert restored == card


def test_sources_prompt_text_is_bounded(tmp_path: Path) -> None:
    _project(tmp_path, overview="x" * 20_000)
    sources = gather_sources(str(tmp_path), model="cheap/model")
    assert len(sources.prompt_text(max_chars=500)) <= 560
    assert "truncated" in sources.prompt_text(max_chars=500)


# ---- Service: caching, opt-in, budget, degradation ---------------------------


async def test_card_is_built_once_and_reused(tmp_path: Path, store_path: Path) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider)
        first = await service.card_for_session("s1")
        assert first is not None
        assert first.subsystems[0].name == "daemon"
        # The deterministic map is never model-written: it comes from the docs.
        assert dict(first.areas)["design/features/sessions.md"]
        again = await service.card_for_session("s1")
        assert again is not None and again.fingerprint == first.fingerprint
        assert len(provider.calls) == 1, "a cached card must not re-spend"
        # And the row is durable: a fresh service reuses it without a call.
        fresh = _service(store, tmp_path, provider)
        reloaded = await fresh.card_for_session("s1")
        assert reloaded is not None and reloaded.summary == first.summary
        assert len(provider.calls) == 1
    finally:
        store.close()


async def test_edited_docs_invalidate_the_cached_card(tmp_path: Path, store_path: Path) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider)
        first = await service.card_for_session("s1")
        assert first is not None
        _project(tmp_path, overview="swe-mux became a build system.")
        provider.value = {"summary": "A build system.", "subsystems": []}
        second = await service.card_for_session("s1")
        assert second is not None
        assert second.summary == "A build system."
        assert second.fingerprint != first.fingerprint
        assert len(provider.calls) == 2
    finally:
        store.close()


async def test_a_touch_without_a_content_change_does_not_rebuild(
    tmp_path: Path, store_path: Path
) -> None:
    """mtime is the cheap staleness check; content is the authority."""
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider)
        assert await service.card_for_session("s1") is not None
        overview = tmp_path / ".docs" / "00_OVERVIEW.md"
        overview.write_text(overview.read_text(encoding="utf-8"), encoding="utf-8")
        assert await service.card_for_session("s1") is not None
        assert len(provider.calls) == 1
    finally:
        store.close()


async def test_a_project_that_did_not_opt_in_gets_no_card(
    tmp_path: Path, store_path: Path
) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider, enabled=False)
        assert await service.card_for_session("s1") is None
        assert await service.card_for_project("p1", str(tmp_path)) is None
        assert provider.calls == []
    finally:
        store.close()


async def test_no_model_configured_degrades_to_no_card(
    tmp_path: Path, store_path: Path
) -> None:
    """Never a guess: with no provider the card is simply absent."""
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    config = FakeConfig(openrouter_cheap_model="", project_card_model="")
    try:
        service = _service(store, tmp_path, provider, config=config)
        assert await service.card_for_session("s1") is None
        assert await service.prompt_prefix("s1") == ""
        assert provider.calls == []
        assert "no OpenRouter model" in str(service.status()["last_error"])
    finally:
        store.close()


async def test_provider_failure_degrades_and_backs_off(
    tmp_path: Path, store_path: Path
) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    provider.error = OpenRouterError("upstream is down")
    try:
        service = _service(store, tmp_path, provider)
        assert await service.card_for_session("s1") is None
        # Second call must not re-ask a provider that just failed.
        assert await service.card_for_session("s1") is None
        assert len(provider.calls) == 1
        assert "upstream is down" in str(service.status()["last_error"])
        # Recovery is possible once the backoff lapses.
        service._failures.clear()
        provider.error = None
        assert await service.card_for_session("s1") is not None
    finally:
        store.close()


async def test_empty_model_output_is_not_stored_as_a_card(
    tmp_path: Path, store_path: Path
) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider({"summary": "   ", "subsystems": []})
    try:
        service = _service(store, tmp_path, provider)
        assert await service.card_for_session("s1") is None
        assert await store.project_card("p1") is None
    finally:
        store.close()


async def test_a_project_with_no_docs_gets_no_card(tmp_path: Path, store_path: Path) -> None:
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider)
        assert await service.card_for_session("s1") is None
        assert provider.calls == []
    finally:
        store.close()


async def test_the_build_is_metered_on_the_shared_ledger(
    tmp_path: Path, store_path: Path
) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider)
        assert await service.card_for_session("s1") is not None
        spend = await store.spend(rule_id=PROJECT_CARD_RULE_ID)
        assert spend["tokens"] == 1380
        assert spend["cost_usd"] == pytest.approx(0.002)
    finally:
        store.close()


async def test_an_exhausted_budget_yields_no_card(tmp_path: Path, store_path: Path) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(
            store, tmp_path, provider, config=FakeConfig(project_card_daily_budget_usd=0.0)
        )
        assert await service.card_for_session("s1") is None
        assert provider.calls == []
        assert "budget" in str(service.status()["last_error"])
    finally:
        store.close()


async def test_the_kill_switch_stops_the_card(tmp_path: Path, store_path: Path) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider, config=FakeConfig(automation_enabled=False))
        assert await service.card_for_session("s1") is None
        assert provider.calls == []
    finally:
        store.close()


async def test_prompt_prefix_is_the_rendered_card(tmp_path: Path, store_path: Path) -> None:
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        service = _service(store, tmp_path, provider)
        prefix = await service.prompt_prefix("s1")
        assert prefix.startswith("# Project card")
        assert "src/swe_mux/session.py" in prefix
    finally:
        store.close()


async def test_a_stale_stored_row_is_never_served(tmp_path: Path, store_path: Path) -> None:
    """The cache is keyed on the fingerprint; a mismatch means rebuild, not reuse."""
    _project(tmp_path)
    store = AutomationStore(store_path)
    provider = FakeProvider()
    try:
        stale = ProjectCard(
            project_id="p1",
            summary="This describes a project that no longer exists.",
            subsystems=(),
            areas=(),
            fingerprint="not-the-current-fingerprint",
            overview_source=".docs/00_OVERVIEW.md",
            model="cheap/model",
            built_at=1.0,
        )
        await store.save_project_card(
            project_id="p1",
            project_root=str(tmp_path),
            fingerprint=stale.fingerprint,
            card=stale.snapshot(),
            schema_version=1,
            requested_model="cheap/model",
            resolved_model="cheap/model",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )
        service = _service(store, tmp_path, provider)
        card = await service.card_for_session("s1")
        assert card is not None
        assert card.summary != stale.summary
        assert len(provider.calls) == 1
    finally:
        store.close()


# ---- Enablement registry -----------------------------------------------------


def test_project_card_is_an_implemented_substrate_toggle() -> None:
    automation = registry.REGISTRY["project_card"]
    assert automation.kind == registry.SUBSTRATE
    assert automation.implemented is True
    assert registry.resolve({"project_card"}).is_enabled("project_card")


def test_card_sources_are_a_pure_function_of_the_project(tmp_path: Path) -> None:
    _project(tmp_path)
    first = gather_sources(str(tmp_path), model="m")
    second = gather_sources(str(tmp_path), model="m")
    assert isinstance(first, CardSources)
    assert first.fingerprint == second.fingerprint
    assert first.areas == second.areas
