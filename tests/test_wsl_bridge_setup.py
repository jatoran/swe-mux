"""The WSL bridge setup surface: what it says before you enable it, and what it refuses.

The whole point of this surface is that it answers *before* the decision it informs.
The shipped diagnostic did the opposite - it stayed silent until `wsl_bridge_enabled`
was already on, so a user with WSL and a native agent in it had no way to learn the
bridge existed. These tests pin that behaviour rather than the wiring.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config
from swe_mux.doctor import _wsl_bridge_checks
from swe_mux.server import (
    error_middleware,
    wsl_bridge_firewall_repair,
    wsl_bridge_install,
    wsl_bridge_status,
)

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="WSL is a Windows host feature")


def _app(tmp_path: Any) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["config"] = Config(data_dir=tmp_path, port=8765)
    app.router.add_get("/api/wsl/bridge", wsl_bridge_status)
    app.router.add_post("/api/wsl/bridge/install", wsl_bridge_install)
    app.router.add_post("/api/wsl/bridge/firewall/repair", wsl_bridge_firewall_repair)
    return app


async def test_status_answers_without_the_feature_being_enabled(tmp_path: Any) -> None:
    """A user cannot be asked to turn something on before anything will tell them
    whether it would work."""
    async with TestClient(TestServer(_app(tmp_path))) as client:
        response = await client.get("/api/wsl/bridge")
        assert response.status == 200
        body = await response.json()
    assert body["enabled"] is False
    assert "supported" in body
    # `distros` is always present, so the UI never has to distinguish "no key" from
    # "no distributions".
    assert isinstance(body["distros"], list)


async def test_status_does_not_probe_unless_asked(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inspecting a distribution starts it, which is seconds a settings page must
    not spend unasked."""
    import swe_mux.wsl_bridge as bridge

    probed: list[str] = []
    monkeypatch.setattr(bridge, "wsl_available", lambda: True)
    monkeypatch.setattr(bridge, "list_distros", lambda: ["Ubuntu"])
    monkeypatch.setattr(bridge, "running_distros", set)
    monkeypatch.setattr(bridge, "wsl_adapter_address", lambda: "172.17.96.1")
    monkeypatch.setattr(bridge, "wsl_adapter_subnet", lambda: "172.17.96.0/20")
    monkeypatch.setattr(bridge, "_listening_on", lambda _a, _p: False)

    def _probe(distro: str, **_kwargs: Any) -> Any:
        probed.append(distro)
        return bridge.BridgeStatus(distro, False)

    monkeypatch.setattr(bridge, "bridge_status", _probe)

    async with TestClient(TestServer(_app(tmp_path))) as client:
        body = await (await client.get("/api/wsl/bridge")).json()
        assert probed == []
        assert body["distros"] == [{"name": "Ubuntu", "running": False}]

        body = await (await client.get("/api/wsl/bridge?probe=1")).json()
    assert probed == ["Ubuntu"]
    assert "bridge" in body["distros"][0]


async def test_install_and_repair_refuse_without_an_explicit_gesture(tmp_path: Any) -> None:
    """Neither writes into a machine on a background poll's say-so.

    Installing writes into the user's distribution and the repair raises a UAC
    prompt; a stray poll must be able to do neither.
    """
    async with TestClient(TestServer(_app(tmp_path))) as client:
        response = await client.post("/api/wsl/bridge/install", json={"distro": "Ubuntu"})
        assert response.status == 400
        response = await client.post("/api/wsl/bridge/firewall/repair", json={})
        assert response.status == 400


async def test_install_requires_a_distro(tmp_path: Any) -> None:
    async with TestClient(TestServer(_app(tmp_path))) as client:
        response = await client.post(
            "/api/wsl/bridge/install",
            json={},
            headers={"X-Mux-User-Gesture": "wsl-bridge-install"},
        )
        assert response.status == 400


async def test_firewall_repair_reports_a_missing_adapter_rather_than_guessing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoping the rule needs the WSL subnet. Inventing one would silently widen it."""
    import swe_mux.server as server

    monkeypatch.setattr(server, "wsl_adapter_subnet", lambda: None)
    async with TestClient(TestServer(_app(tmp_path))) as client:
        response = await client.post(
            "/api/wsl/bridge/firewall/repair",
            json={},
            headers={"X-Mux-User-Gesture": "wsl-firewall-repair"},
        )
        assert response.status == 409
        body = await response.json()
    # `unsupported` off a frozen Windows build; `no_wsl_adapter` on one. Both are a
    # refusal to guess, which is the property under test.
    assert body["reason"] in {"no_wsl_adapter", "unsupported"}


def test_doctor_offers_the_bridge_instead_of_going_quiet() -> None:
    """The check that used to be silent exactly when it was needed.

    An `unavailable`/`optional` row would read as a fault; this is an offer, so it
    is `ok`/`info` with a remedy naming the next action.
    """
    checks = _wsl_bridge_checks(
        [
            {
                "distro": "Ubuntu",
                "enabled": False,
                "available": False,
                "installed": False,
                "harnesses": [],
                "reasons": ["the WSL agent bridge is off; enable it in Settings"],
            }
        ]
    )
    assert len(checks) == 1
    assert checks[0]["status"] == "ok"
    assert checks[0]["severity"] == "info"
    assert "Settings" in str(checks[0]["remedy"])


def test_doctor_still_reports_a_real_blocker_as_unavailable() -> None:
    """Turning it on and having it not work is a different answer from not turning
    it on, and the remedy has to say which."""
    checks = _wsl_bridge_checks(
        [
            {
                "distro": "Ubuntu",
                "enabled": True,
                "available": False,
                "installed": False,
                "harnesses": [],
                "reasons": ["Ubuntu cannot reach the daemon at 172.17.96.1:8765"],
            }
        ]
    )
    assert checks[0]["status"] == "unavailable"
    assert "cannot reach the daemon" in str(checks[0]["detail"])


def test_doctor_reports_a_working_bridge_as_ok() -> None:
    checks = _wsl_bridge_checks(
        [
            {
                "distro": "Ubuntu",
                "enabled": True,
                "available": True,
                "installed": True,
                "harnesses": [{"name": "claude", "executable": "/home/u/.local/bin/claude"}],
                "reasons": [],
            }
        ]
    )
    assert checks[0]["status"] == "ok"
    assert checks[0]["severity"] == "optional"
    assert "claude" in str(checks[0]["detail"])


@WINDOWS_ONLY
def test_setup_status_reports_the_restart_a_flipped_toggle_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabling the flag changes which sockets the daemon binds, and that only
    happens at startup. Silence here is "I turned it on and nothing happened"."""
    import swe_mux.wsl_bridge as bridge

    monkeypatch.setattr(bridge, "wsl_available", lambda: True)
    monkeypatch.setattr(bridge, "list_distros", list)
    monkeypatch.setattr(bridge, "running_distros", set)
    monkeypatch.setattr(bridge, "wsl_adapter_address", lambda: "172.17.96.1")
    monkeypatch.setattr(bridge, "wsl_adapter_subnet", lambda: "172.17.96.0/20")
    monkeypatch.setattr(bridge, "_listening_on", lambda _a, _p: False)

    status = bridge.setup_status(daemon_port=8765, enabled=True)
    assert status["restart_required"] is True

    monkeypatch.setattr(bridge, "_listening_on", lambda _a, _p: True)
    assert bridge.setup_status(daemon_port=8765, enabled=True)["restart_required"] is False
    # Off, there is nothing to restart *for* - the answer is "enable it", not
    # "restart", and offering both would be noise.
    assert bridge.setup_status(daemon_port=8765, enabled=False)["restart_required"] is False


def _unused(*_args: object, **_kwargs: object) -> SimpleNamespace:  # pragma: no cover
    return SimpleNamespace()
