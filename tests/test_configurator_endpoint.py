"""The configurator launcher's two routes, and the settings write behind them.

What these hold is the part a unit test of `configurator.py` cannot: that the
button's refusals are the *right* refusals (a missing CLI and a missing Project
are different answers with different codes), that the marker which grants the
whole tool family is set by this endpoint and by nothing else, and that a
rejected settings batch changes nothing at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import server
from swe_mux.config import Config
from swe_mux.server import (
    _configurator_apply_settings,
    _configurator_harness,
    _configurator_health_line,
    _configurator_project,
    configurator_options,
    launch_configurator,
)
from swe_mux.spawn_contract import SpawnRequest


class EventsStub:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **payload: Any) -> None:
        self.emitted.append((name, payload))


def _project(pid: str, root: Path) -> Any:
    return SimpleNamespace(id=pid, name=pid, root=str(root))


def _app(config: Config, *projects: Any) -> dict[str, Any]:
    table = {item.id: item for item in projects}
    return {
        "config": config,
        "events": EventsStub(),
        "projects": SimpleNamespace(
            projects=table, ordered_projects=lambda: list(projects)
        ),
    }


def _request(app: dict[str, Any], body: dict[str, Any] | None = None) -> Any:
    async def read() -> dict[str, Any]:
        return body or {}

    return SimpleNamespace(app=app, json=read, can_read_body=body is not None, query={})


async def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.text)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")


# ------------------------------------------------------------ harness choice


def test_an_explicit_harness_must_be_one_that_is_actually_available(config: Config) -> None:
    assert _configurator_harness(config, "codex", ("claude", "codex")) == "codex"
    # Not a silent substitution: the operator picked a specific agent from a menu,
    # and quietly launching a different one is worse than saying it is gone.
    assert _configurator_harness(config, "codex", ("claude",)) is None


def test_the_configured_default_outranks_the_run_menu_default(config: Config) -> None:
    config.default_harness = "codex"
    config.default_backend = "claude"
    assert _configurator_harness(config, "", ("claude", "codex")) == "codex"


def test_a_shell_run_menu_default_falls_through_to_an_agent(config: Config) -> None:
    config.default_backend = "shell"
    assert _configurator_harness(config, "", ("claude",)) == "claude"


# ------------------------------------------------------------ project choice


def test_an_explicit_project_wins(tmp_path: Path, config: Config) -> None:
    first, second = _project("p1", tmp_path / "a"), _project("p2", tmp_path / "b")
    app = _app(config, first, second)
    assert _configurator_project(app, "p2") is second


def test_a_source_checkout_project_is_preferred_over_merely_the_first(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where swe-mux's own code is, is where code changes are possible.

    Only matters on a source install; everywhere else this branch never fires and
    the first Project is the answer.
    """
    checkout = tmp_path / "swe-mux"
    checkout.mkdir()
    monkeypatch.setattr(server, "source_checkout", lambda: checkout)
    app = _app(config, _project("p1", tmp_path / "other"), _project("p2", checkout))
    assert _configurator_project(app, "").id == "p2"


def test_no_project_at_all_is_none_rather_than_an_invented_one(config: Config) -> None:
    assert _configurator_project(_app(config), "") is None


# ------------------------------------------------------------------- options


@pytest.mark.asyncio
async def test_options_report_what_the_button_needs_before_it_is_pressed(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_configurator_candidates", lambda _config: ("claude",))
    body = await _body(
        await configurator_options(_request(_app(config, _project("p1", tmp_path))))
    )
    assert body["harnesses"] == ["claude"]
    assert body["default_harness"] == "claude"
    assert body["projects"] == 1
    assert body["install_mode"] in {"source", "frozen", "installed"}


# -------------------------------------------------------------------- launch


@pytest.mark.asyncio
async def test_a_launch_seeds_a_prompt_and_marks_the_session(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[dict[str, Any]] = []
    published: list[bool] = []
    record = SimpleNamespace(id="s1", configurator=False, snapshot=lambda: {"id": "s1"})
    session = SimpleNamespace(record=record, publish_update=lambda: published.append(True))

    async def spawn(_app: Any, body: dict[str, Any]) -> Any:
        spawned.append(body)
        return session

    monkeypatch.setattr(server, "_configurator_candidates", lambda _config: ("claude",))
    monkeypatch.setattr(server, "_spawn_from_body", spawn)
    monkeypatch.setattr(server, "_doctor_report", lambda _app: _ok_report())
    monkeypatch.setattr(
        server, "detect_installations_with_versions", lambda _overrides: {}
    )

    app = _app(config, _project("p1", tmp_path))
    response = await launch_configurator(_request(app, {}))
    assert response.status == 201

    assert len(spawned) == 1
    body = spawned[0]
    assert body["backend"] == "claude"
    assert body["project_id"] == "p1"
    # Seeded, not staged: the human pressed a button whose label says it starts a
    # conversation, so leaving the opening turn unsent answers a different press.
    assert "stage_text" not in body
    assert "swe-mux configurator" in body["seed_text"]

    # The marker is set here and republished, so every attached client sees it.
    assert record.configurator is True
    assert published == [True]
    assert app["events"].emitted[0][0] == "configurator_launched"


@pytest.mark.asyncio
async def test_the_launch_body_is_a_valid_spawn_request(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint composes a body by hand; keep it parseable.

    `SpawnRequest.parse` refuses unknown fields, so this is also what would catch
    someone adding `configurator` to the launch body - which must never become a
    spawn field, or `request_spawn` becomes a way for any agent to ask for a
    session that can rewrite this install.
    """
    captured: list[dict[str, Any]] = []

    async def spawn(_app: Any, body: dict[str, Any]) -> Any:
        captured.append(body)
        SpawnRequest.parse(dict(body))
        return SimpleNamespace(
            record=SimpleNamespace(id="s1", configurator=False, snapshot=lambda: {}),
            publish_update=lambda: None,
        )

    monkeypatch.setattr(server, "_configurator_candidates", lambda _config: ("claude",))
    monkeypatch.setattr(server, "_spawn_from_body", spawn)
    monkeypatch.setattr(server, "_doctor_report", lambda _app: _ok_report())
    monkeypatch.setattr(server, "detect_installations_with_versions", lambda _o: {})

    await launch_configurator(_request(_app(config, _project("p1", tmp_path)), {}))
    assert "configurator" not in captured[0]


@pytest.mark.asyncio
async def test_no_harness_and_no_project_refuse_with_different_codes(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_configurator_candidates", lambda _config: ())
    no_harness = await launch_configurator(_request(_app(config, _project("p1", tmp_path)), {}))
    assert no_harness.status == 409
    assert (await _body(no_harness))["code"] == "no_harness"

    monkeypatch.setattr(server, "_configurator_candidates", lambda _config: ("claude",))
    no_project = await launch_configurator(_request(_app(config), {}))
    assert no_project.status == 409
    assert (await _body(no_project))["code"] == "no_project"


@pytest.mark.asyncio
async def test_an_unavailable_named_harness_names_the_ones_that_are(
    tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "_configurator_candidates", lambda _config: ("claude",))
    response = await launch_configurator(
        _request(_app(config, _project("p1", tmp_path)), {"harness": "codex"})
    )
    body = await _body(response)
    assert body["code"] == "no_harness"
    assert body["candidates"] == ["claude"]


# ------------------------------------------------------------------- writes


@pytest.mark.asyncio
async def test_a_valid_batch_applies_and_reports_which_half_needs_a_restart(
    config: Config,
) -> None:
    app = _app(config)
    result = await _configurator_apply_settings(app, {"theme": "nord", "port": 8799})
    assert result["applied"] is True
    assert result["hot_applied"] == ["theme"]
    # `port` cannot take effect without a restart, and reporting it as done is how
    # a working setting reads as a broken one.
    assert result["restart_required"] == ["port"]
    assert config.theme == "nord"
    assert app["events"].emitted[0][1]["source"] == "configurator"


@pytest.mark.asyncio
async def test_an_invalid_batch_changes_nothing_and_names_the_field(config: Config) -> None:
    before = config.theme
    result = await _configurator_apply_settings(
        config_app := _app(config), {"theme": "nord", "port": 0}
    )
    assert result["applied"] is False
    assert "port" in result["errors"]
    # All-or-nothing: `_validate` runs over the whole candidate before anything is
    # written, so the legal half of a rejected batch must not have landed either.
    assert config.theme == before
    assert config_app["events"].emitted == []


@pytest.mark.asyncio
async def test_an_unknown_setting_is_refused_rather_than_silently_dropped(
    config: Config,
) -> None:
    result = await _configurator_apply_settings(_app(config), {"not_a_setting": 1})
    assert result["applied"] is False
    assert "not_a_setting" in result["errors"]


# ------------------------------------------------------------- health summary


def _ok_report() -> Any:
    async def report() -> dict[str, Any]:
        return {"checks": [{"id": "daemon", "status": "ok", "severity": "info"}]}

    return report()


def test_a_clean_report_says_so_in_one_sentence() -> None:
    line = _configurator_health_line({"checks": [{"id": "a", "status": "ok"}]})
    assert "every check passes" in line


def test_an_unhealthy_report_leads_with_the_critical_ones() -> None:
    line = _configurator_health_line(
        {
            "checks": [
                {"id": "a", "status": "warn", "severity": "optional", "title": "Tailscale"},
                {"id": "b", "status": "fail", "severity": "critical", "title": "Supervisor"},
            ]
        }
    )
    assert "2 check(s)" in line
    assert "1 critical" in line
    assert "Supervisor" in line
    # Severity ranks, position in the list does not: with a critical present the
    # named titles are the critical ones, so the optional warning is counted and
    # not named. A summary that led with "Tailscale" would send the reader at the
    # wrong problem.
    assert "Tailscale" not in line


def test_a_report_with_no_checks_block_adds_nothing() -> None:
    assert _configurator_health_line({}) == ""


@pytest.mark.asyncio
async def test_a_slow_health_report_does_not_hold_up_the_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary is a nicety; the button press is the thing that must be fast.

    The full report inspects the host firewall and probes CLI versions, either of
    which can stall. Degrading to no summary is strictly better than a launch
    that appears to hang, and the prompt's fallback line still tells the agent
    where to look.
    """
    import asyncio

    async def never() -> dict[str, Any]:
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(server, "CONFIGURATOR_HEALTH_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(server, "_doctor_report", lambda _app: never())
    assert await server._configurator_health_preview({}) == ""


@pytest.mark.asyncio
async def test_a_failing_health_report_does_not_fail_the_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken() -> dict[str, Any]:
        raise RuntimeError("firewall probe exploded")

    monkeypatch.setattr(server, "_doctor_report", lambda _app: broken())
    assert await server._configurator_health_preview({}) == ""
