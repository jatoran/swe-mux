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
from swe_mux.experience_tiers import _DETERMINISTIC, TIERS, tier_changes
from swe_mux.harness import HARNESSES
from swe_mux.routes.settings import apply_experience_tier


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
