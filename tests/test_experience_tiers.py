"""Experience tiers: absolute assignments over ordinary config keys.

The claims that make a tier safe to offer, each pinned: the deterministic tier
is byte-identical to a fresh install, every tier's assignment passes the
ordinary validator, the assignment is absolute (switching tiers fully undoes
the previous one), and nothing gates capability on the tier value itself.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config, update_config
from swe_mux.experience_tiers import (
    _DETERMINISTIC,
    _SUPERVISED,
    AUTONOMY_LEVELS,
    OVERRIDABLE_KEYS,
    TIERS,
    autonomy_changes,
    tier_changes,
)
from swe_mux.harness import HARNESSES
from swe_mux.routes.settings import apply_experience_tier, get_experience_tiers


def _fresh(tmp_path: Path) -> Config:
    config = Config(data_dir=tmp_path)
    config.config_path = tmp_path / "config.toml"
    return config


def test_deterministic_equals_a_fresh_install(tmp_path: Path) -> None:
    """Choosing "deterministic" on a new machine must change nothing but the
    stamp, so the tier table is checked against the dataclass defaults rather
    than trusted to restate them."""
    defaults = {field.name: getattr(Config(), field.name) for field in dataclasses.fields(Config)}
    for key, value in _DETERMINISTIC.items():
        assert defaults[key] == value, key
    config = _fresh(tmp_path)
    before = {key: getattr(config, key) for key in _DETERMINISTIC}
    update_config(config, tier_changes("deterministic"))
    assert config.experience_tier == "deterministic"
    assert {key: getattr(config, key) for key in _DETERMINISTIC} == before


def test_every_tier_assignment_passes_the_ordinary_validator(tmp_path: Path) -> None:
    for tier in TIERS:
        update_config(_fresh(tmp_path / tier), tier_changes(tier))


def test_terminal_switches_off_everything_that_watches(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    update_config(config, tier_changes("terminal"))
    assert set(config.harness_instrument_enabled) == set(HARNESSES)
    assert not any(config.harness_instrument_enabled.values())
    assert set(config.harness_mcp_enabled) == set(HARNESSES)
    assert not any(config.harness_mcp_enabled.values())
    assert config.agent_shims_on_shell_path is False
    assert config.agent_messaging_enabled is False
    assert config.land_queue_enabled is False
    # The model-backed layer stays off; terminal is not "deterministic minus".
    assert config.automation_enabled is False


def test_automations_turns_on_exactly_the_three_masters(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    update_config(config, tier_changes("automations"))
    assert config.automation_enabled is True
    assert config.scan_timeline_enabled is True
    assert config.attention_observers_enabled is True
    # Everything deterministic keeps its default; budgets are untouched.
    assert config.agent_messaging_enabled is True
    assert config.harness_instrument_enabled == {}


def test_the_assignment_is_absolute_so_switching_back_restores_defaults(
    tmp_path: Path,
) -> None:
    config = _fresh(tmp_path)
    update_config(config, tier_changes("terminal"))
    update_config(config, tier_changes("deterministic"))
    fresh = Config()
    for key in _DETERMINISTIC:
        assert getattr(config, key) == getattr(fresh, key), key


def test_an_unknown_tier_is_refused() -> None:
    with pytest.raises(ValueError):
        tier_changes("expert")


# ---------------------------------------------------------------- autonomy axis


def test_supervised_equals_a_fresh_install(tmp_path: Path) -> None:
    """Declining the autonomy choice must change nothing, so the supervised
    table is checked against the dataclass defaults exactly the way the
    deterministic tier's is."""
    defaults = {field.name: getattr(Config(), field.name) for field in dataclasses.fields(Config)}
    for key, value in _SUPERVISED.items():
        assert defaults[key] == value, key
    config = _fresh(tmp_path)
    before = {key: getattr(config, key) for key in _SUPERVISED}
    update_config(config, autonomy_changes("supervised"))
    assert {key: getattr(config, key) for key in _SUPERVISED} == before


def test_every_autonomy_assignment_passes_the_ordinary_validator(tmp_path: Path) -> None:
    for level in AUTONOMY_LEVELS:
        update_config(_fresh(tmp_path / level), autonomy_changes(level))


def test_assisted_turns_on_delivery_under_the_shipped_bounds(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    update_config(config, autonomy_changes("assisted"))
    assert config.auto_delivery_enabled is True
    assert config.auto_delivery_max_consecutive == Config().auto_delivery_max_consecutive


def test_autonomous_widens_the_bounds_and_switching_back_restores_them(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    update_config(config, autonomy_changes("autonomous"))
    assert config.auto_delivery_enabled is True
    assert config.auto_delivery_max_consecutive == 10
    assert config.auto_delivery_session_ttl_minutes == 120
    assert config.auto_delivery_reply_window_minutes == 60
    assert config.agent_spawn_hourly_budget == 20
    update_config(config, autonomy_changes("supervised"))
    fresh = Config()
    for key in _SUPERVISED:
        assert getattr(config, key) == getattr(fresh, key), key


def test_an_unknown_autonomy_level_is_refused() -> None:
    with pytest.raises(ValueError):
        autonomy_changes("yolo")


def test_the_overridable_set_is_exactly_the_tier_inventory_booleans() -> None:
    """The per-harness maps and the tier stamp are deliberately not overridable:
    the maps have their own Settings surface, and the stamp is never a
    deviation."""
    assert OVERRIDABLE_KEYS == {
        key for key, value in _DETERMINISTIC.items() if isinstance(value, bool)
    }
    assert "experience_tier" not in OVERRIDABLE_KEYS
    assert "harness_mcp_enabled" not in OVERRIDABLE_KEYS


def test_nothing_outside_the_tier_module_gates_on_the_tier() -> None:
    """"Tiers set defaults; they do not lock capability" - enforced, not
    remembered: no backend module may read `experience_tier` to decide
    behaviour. Density defaults in the frontend are the one sanctioned reader
    class, and they choose presentation, not capability."""
    root = Path(__file__).resolve().parent.parent / "src" / "swe_mux"
    allowed = {"config.py", "experience_tiers.py", "settings.py"}
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path.name not in allowed and "experience_tier" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


# ------------------------------------------------------------------- the route


class _FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


class _FakeRequest:
    def __init__(self, app: dict[Any, Any], body: Any) -> None:
        self.app = app
        self._body = body
        self.headers: dict[str, str] = {}
        self.query: dict[str, str] = {}

    async def json(self) -> Any:
        return self._body


async def test_the_route_applies_and_reports_restart_scope(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    app = {keys.CONFIG: config, keys.EVENTS: _FakeEvents()}
    response = await apply_experience_tier(cast(Any, _FakeRequest(app, {"tier": "terminal"})))
    payload = json.loads(response.text or "")
    assert payload["experience_tier"] == "terminal"
    # The per-harness maps are restart-scoped, and the response must say so
    # rather than reporting a hot apply that never reached the adapters.
    assert "harness_instrument_enabled" in payload["restart_required"]
    assert (tmp_path / "config.toml").is_file()


async def test_the_route_refuses_an_unknown_tier(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    app = {keys.CONFIG: config, keys.EVENTS: _FakeEvents()}
    response = await apply_experience_tier(cast(Any, _FakeRequest(app, {"tier": "expert"})))
    assert response.status == 422
    assert config.experience_tier == ""


async def test_the_route_applies_autonomy_and_overrides_atomically(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    app = {keys.CONFIG: config, keys.EVENTS: _FakeEvents()}
    body = {
        "tier": "deterministic",
        "autonomy": "autonomous",
        "overrides": {"land_queue_enabled": False},
    }
    response = await apply_experience_tier(cast(Any, _FakeRequest(app, body)))
    assert response.status == 200
    assert config.experience_tier == "deterministic"
    assert config.auto_delivery_enabled is True
    assert config.auto_delivery_max_consecutive == 10
    assert config.land_queue_enabled is False
    # Everything the override did not name follows the tier.
    assert config.agent_messaging_enabled is True


async def test_the_route_refuses_an_unknown_override_key_without_writing(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    app = {keys.CONFIG: config, keys.EVENTS: _FakeEvents()}
    body = {"tier": "deterministic", "overrides": {"automation_hourly_call_cap": 9999}}
    response = await apply_experience_tier(cast(Any, _FakeRequest(app, body)))
    assert response.status == 422
    assert config.experience_tier == ""
    assert config.automation_hourly_call_cap == Config().automation_hourly_call_cap


async def test_the_route_refuses_an_unknown_autonomy_level(tmp_path: Path) -> None:
    config = _fresh(tmp_path)
    app = {keys.CONFIG: config, keys.EVENTS: _FakeEvents()}
    body = {"tier": "deterministic", "autonomy": "yolo"}
    response = await apply_experience_tier(cast(Any, _FakeRequest(app, body)))
    assert response.status == 422
    assert config.experience_tier == ""


async def test_the_preview_route_serves_every_assignment(tmp_path: Path) -> None:
    """The panel draws what a tier sets from this payload rather than from a
    browser-side copy, so the payload must carry every tier, every autonomy
    level, and the closed override set."""
    config = _fresh(tmp_path)
    app = {keys.CONFIG: config, keys.EVENTS: _FakeEvents()}
    response = await get_experience_tiers(cast(Any, _FakeRequest(app, {})))
    payload = json.loads(response.text or "")
    assert set(payload["tiers"]) == set(TIERS)
    assert set(payload["autonomy"]) == set(AUTONOMY_LEVELS)
    assert set(payload["overridable"]) == OVERRIDABLE_KEYS
    assert payload["tiers"]["terminal"]["agent_messaging_enabled"] is False
    assert payload["autonomy"]["autonomous"]["auto_delivery_enabled"] is True
