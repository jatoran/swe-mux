"""Who may take the single Tailscale Serve route, and who may not.

There is one HTTPS route and several daemons that might want it. The shipped rule -
reclaim an abandoned swe-mux route, never touch a foreign one - is right and is
preserved here. What these tests pin is the case it missed: a *running* swe-mux
already owns it.

That failure is invisible from the taking side. The victim keeps working on
loopback and never learns it lost the route; only the phone notices, and only once
the thief exits, at which point the tailnet URL answers nothing. It cost real
mobile access on 2026-08-17, which is why it is tested rather than commented.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from swe_mux import tailscale
from swe_mux.bounded_subprocess import ProcessOutcome


def _ok() -> ProcessOutcome:
    """What a `tailscale serve --bg` that succeeded quietly reports."""
    return ProcessOutcome(
        exit_code=0,
        stdout=b"",
        stderr=b"",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=1.0,
        timed_out=False,
    )


def _serve(port: int, https_port: int = 443) -> dict[str, Any]:
    """A Serve config shaped like `tailscale serve status --json`."""
    host = "desktop.taild42d36.ts.net"
    authority = host if https_port == 443 else f"{host}:{https_port}"
    return {"Web": {authority: {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}}}


def test_the_loopback_target_port_is_readable() -> None:
    """Knowing *which* daemon holds the route is what makes the decision possible.

    "Is it loopback" cannot distinguish an abandoned route from a live one.
    """
    assert tailscale._loopback_target_port(_serve(8765), 443) == 8765
    assert tailscale._loopback_target_port(_serve(18823), 443) == 18823
    # A foreign, non-loopback route has no port to reclaim.
    foreign = {"Web": {"desktop.taild42d36.ts.net": {"Handlers": {"/": {"Proxy": "http://10.0.0.5:3000"}}}}}
    assert tailscale._loopback_target_port(foreign, 443) is None
    assert tailscale._loopback_target_port({}, 443) is None


async def test_a_running_swemux_keeps_its_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case that broke mobile access: a second daemon must not take the address."""
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")

    async def status(_exe: str, command: str) -> tuple[Any | None, str]:
        return (_serve(8765), "") if command == "serve" else (None, "not configured")

    monkeypatch.setattr(tailscale, "_status", status)
    # The current owner answers: it is alive.
    async def alive(_port: int) -> bool:
        return True

    monkeypatch.setattr(tailscale, "_swemux_daemon_alive", alive)

    result = await tailscale.enable_mobile_voice_serve(18823)
    assert result["status"] == "error"
    detail = str(result["diagnostic"])
    # The message has to name the holder and the way out, or the next person hits
    # exactly the confusion this fix exists to end.
    assert "8765" in detail
    assert "--local-only" in detail


async def test_an_abandoned_route_is_still_reclaimable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reclaiming a dead daemon's route is the shipped behaviour and stays.

    Losing it would strand the route forever after any unclean daemon exit.
    """
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")

    # Stateful, because the function verifies the route stuck after configuring it.
    # A mock frozen on the old config would fail that check and hide whether the
    # takeover was allowed at all.
    state = {"port": 18823}

    async def status(_exe: str, command: str) -> tuple[Any | None, str]:
        return (_serve(state["port"]), "") if command == "serve" else (None, "not configured")

    monkeypatch.setattr(tailscale, "_status", status)

    async def dead(_port: int) -> bool:
        return False

    monkeypatch.setattr(tailscale, "_swemux_daemon_alive", dead)

    configured: list[tuple[str, ...]] = []

    async def fake_run_bounded(argv: Sequence[str], **_kwargs: Any) -> ProcessOutcome:
        configured.append(tuple(argv))
        state["port"] = 8765
        return _ok()

    monkeypatch.setattr(tailscale, "run_bounded", fake_run_bounded)

    async def ipv4(_exe: str | None = None) -> str | None:
        return "100.64.1.2"

    monkeypatch.setattr(tailscale, "tailscale_ipv4", ipv4)
    result = await tailscale.enable_mobile_voice_serve(8765)
    assert result["status"] != "error", result
    assert any("http://127.0.0.1:8765" in arg for args in configured for arg in args)


async def test_a_foreign_route_is_never_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged, and the reason is unchanged: it is somebody else's service."""
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")
    foreign = {
        "Web": {
            "desktop.taild42d36.ts.net": {"Handlers": {"/": {"Proxy": "http://10.0.0.5:3000"}}}
        }
    }

    async def status(_exe: str, command: str) -> tuple[Any | None, str]:
        return (foreign, "") if command == "serve" else (None, "not configured")

    monkeypatch.setattr(tailscale, "_status", status)
    result = await tailscale.enable_mobile_voice_serve(8765)
    assert result["status"] == "error"
    assert "another private Tailscale route" in str(result["diagnostic"])


async def test_a_daemon_reconfiguring_its_own_route_is_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repairing your own route must not be mistaken for stealing someone else's."""
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")

    async def status(_exe: str, command: str) -> tuple[Any | None, str]:
        return (_serve(8765), "") if command == "serve" else (None, "not configured")

    monkeypatch.setattr(tailscale, "_status", status)

    async def alive(_port: int) -> bool:  # pragma: no cover - must not be consulted
        raise AssertionError("liveness must not be probed for the daemon's own route")

    monkeypatch.setattr(tailscale, "_swemux_daemon_alive", alive)

    async def fake_run_bounded(_argv: Sequence[str], **_kwargs: Any) -> ProcessOutcome:
        return _ok()

    monkeypatch.setattr(tailscale, "run_bounded", fake_run_bounded)

    async def ipv4(_exe: str | None = None) -> str | None:
        return "100.64.1.2"

    monkeypatch.setattr(tailscale, "tailscale_ipv4", ipv4)
    result = await tailscale.enable_mobile_voice_serve(8765)
    assert result["status"] != "error", result
