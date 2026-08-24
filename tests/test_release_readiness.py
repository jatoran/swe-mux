"""Tests for the new-user release-readiness P0 work.

Covers the Tailscale connection-state classifier, the Windows Defender Firewall
inspect/repair logic (with a mocked PowerShell runner, no real firewall), and the
one-click diagnostics export endpoint.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import swe_mux.harness as harness_module
import swe_mux.server as server
import swe_mux.tailscale as tailscale
import swe_mux.windows_firewall as firewall
from swe_mux import app_keys as keys
from swe_mux.adapters import ClaudeAdapter, CodexAdapter, SpawnOptions
from swe_mux.config import Config, load_config, update_config
from swe_mux.harness import (
    HarnessInstallation,
    detect_installations_with_versions,
    public_harness_registry,
    version_is_untested,
)
from swe_mux.prerequisites import detect_prerequisites
from swe_mux.routes import diagnostics as diagnostics_routes
from swe_mux.routes import system as system_routes
from swe_mux.routes.diagnostics import diagnostics_export, get_doctor_report
from swe_mux.routes.system import firewall_repair, firewall_status
from swe_mux.server import error_middleware

# --------------------------------------------------------------------------- #
# Tailscale connection-state classifier
# --------------------------------------------------------------------------- #


def test_classify_not_installed() -> None:
    result = tailscale.classify_tailscale_connection(False, None)
    assert result["connection_state"] == "not_installed"
    assert result["device_name"] is None
    assert "winget" in str(result["connection_command"])


def test_classify_installed_but_status_unreadable() -> None:
    result = tailscale.classify_tailscale_connection(True, None)
    assert result["connection_state"] == "unknown"
    assert result["connection_command"] == "tailscale status"


@pytest.mark.parametrize("backend", ["NeedsLogin", "NoState", ""])
def test_classify_logged_out(backend: str) -> None:
    result = tailscale.classify_tailscale_connection(True, {"BackendState": backend})
    assert result["connection_state"] == "logged_out"
    assert result["connection_command"] == "tailscale login"


def test_classify_stopped_offers_up() -> None:
    result = tailscale.classify_tailscale_connection(True, {"BackendState": "Stopped"})
    assert result["connection_state"] == "stopped"
    assert result["connection_command"] == "tailscale up"


def test_classify_connected_reports_device_name() -> None:
    payload = {"BackendState": "Running", "Self": {"DNSName": "desk.tail1234.ts.net."}}
    result = tailscale.classify_tailscale_connection(True, payload)
    assert result["connection_state"] == "connected"
    # The trailing dot from MagicDNS is stripped for display.
    assert result["device_name"] == "desk.tail1234.ts.net"
    assert result["connection_command"] is None
    assert "desk.tail1234.ts.net" in str(result["connection_detail"])


def test_classify_needs_machine_auth() -> None:
    result = tailscale.classify_tailscale_connection(True, {"BackendState": "NeedsMachineAuth"})
    assert result["connection_state"] == "needs_machine_auth"


async def test_probe_merges_connection_fields_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailscale.shutil, "which", lambda _name: None)
    result = await tailscale._probe_tailscale_status(8765, tailnet_enabled=True)
    assert result["available"] is False
    assert result["connection_state"] == "not_installed"


async def test_probe_reports_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailscale.shutil, "which", lambda _name: "tailscale")

    async def fake_serve_or_funnel(_exe: str, _command: str) -> tuple[Any | None, str]:
        return None, "not configured"

    async def fake_plain_status(_exe: str) -> Any:
        return {"BackendState": "Running", "Self": {"DNSName": "host.tailnet.ts.net."}}

    async def fake_ipv4(_exe: str | None = None) -> str | None:
        return "100.100.100.100"

    monkeypatch.setattr(tailscale, "_status", fake_serve_or_funnel)
    monkeypatch.setattr(tailscale, "_plain_status", fake_plain_status)
    monkeypatch.setattr(tailscale, "tailscale_ipv4", fake_ipv4)
    result = await tailscale._probe_tailscale_status(8765, tailnet_enabled=True)
    assert result["connection_state"] == "connected"
    assert result["device_name"] == "host.tailnet.ts.net"
    assert result["tailnet_ip"] == "100.100.100.100"


# --------------------------------------------------------------------------- #
# Windows Defender Firewall scope + inspection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "scope,covered",
    [
        ("Any", True),
        ("any4", True),
        ("LocalSubnet", False),
        ("Any6", False),
        ("100.64.0.0/10", True),
        ("100.0.0.0/8", True),  # a superset of the tailnet range
        ("100.64.0.0/24", False),  # too narrow
        ("100.115.92.7", False),  # a single desktop host
        ("100.64.0.0-100.127.255.255", True),  # the exact tailnet range as a dash range
        ("10.0.0.0/8", False),  # a LAN range, not the tailnet
        ("garbage", False),
    ],
)
def test_scope_covers_tailnet(scope: str, covered: bool) -> None:
    assert firewall.scope_covers_tailnet(scope) is covered


def test_interpret_blocking_rule_needs_repair() -> None:
    parsed = {
        "blockingRuleDetected": True,
        "privateFirewallEnabled": True,
        "matchingRuleScopes": [],
    }
    result = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed)
    assert result["blocking_rule_detected"] is True
    assert result["rule_allowed"] is False
    assert result["needs_repair"] is True


def test_interpret_missing_allow_needs_repair() -> None:
    parsed = {
        "blockingRuleDetected": False,
        "privateFirewallEnabled": True,
        "matchingRuleScopes": [],
    }
    result = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed)
    assert result["needs_repair"] is True
    assert result["rule_allowed"] is False


def test_interpret_sufficient_allow_is_clean() -> None:
    parsed = {
        "blockingRuleDetected": False,
        "privateFirewallEnabled": True,
        "matchingRuleScopes": [{"remoteAddresses": ["Any"]}],
    }
    result = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed)
    assert result["rule_allowed"] is True
    assert result["needs_repair"] is False


def test_interpret_disabled_private_profile_needs_nothing() -> None:
    parsed = {
        "blockingRuleDetected": False,
        "privateFirewallEnabled": False,
        "matchingRuleScopes": [],
    }
    result = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed)
    assert result["needs_repair"] is False
    assert "disabled" in str(result["detail"])


def test_desktop_only_allow_scope_still_needs_repair() -> None:
    # A rule scoped to just this desktop's tailnet address does not admit the
    # phone, so the inspection must still flag a repair.
    parsed = {
        "blockingRuleDetected": False,
        "privateFirewallEnabled": True,
        "matchingRuleScopes": [{"remoteAddresses": ["100.115.92.7"]}],
    }
    result = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed)
    assert result["needs_repair"] is True


def test_serve_active_suppresses_the_repair_alarm() -> None:
    # A missing Allow rule blocks the direct 100.x path, but when Tailscale Serve
    # proxies the port over loopback the phone never uses that path, so no alarm.
    parsed = {
        "blockingRuleDetected": False,
        "privateFirewallEnabled": True,
        "matchingRuleScopes": [],
    }
    serve_down = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed, serve_active=False)
    assert serve_down["needs_repair"] is True
    serve_up = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed, serve_active=True)
    assert serve_up["needs_repair"] is False
    assert serve_up["direct_path_blocked"] is True
    assert "Serve" in str(serve_up["detail"])


def test_serve_active_downgrades_a_blocking_rule_to_a_note() -> None:
    parsed = {
        "blockingRuleDetected": True,
        "privateFirewallEnabled": True,
        "matchingRuleScopes": [],
    }
    serve_up = firewall.interpret_inspection(8765, r"C:\swe-mux.exe", parsed, serve_active=True)
    assert serve_up["needs_repair"] is False
    assert serve_up["blocking_rule_detected"] is True
    assert serve_up["direct_path_blocked"] is True


def test_inspection_script_quotes_program_and_port() -> None:
    script = firewall.build_inspection_script(8765, r"C:\Program Files\swe-mux.exe")
    assert "'C:\\Program Files\\swe-mux.exe'" in script
    assert "'8765'" in script
    assert "ActiveStore" in script


def test_repair_script_adds_scoped_allow_rule() -> None:
    script = firewall.build_repair_script(8765, r"C:\swe-mux.exe")
    assert "New-NetFirewallRule" in script
    assert "-Profile Private" in script
    assert "-LocalPort 8765" in script
    assert firewall.FIREWALL_RULE_NAME in script


async def test_inspect_firewall_unsupported_off_frozen() -> None:
    # In a source/test run firewall_supported() is false, so no PowerShell runs.
    result = await firewall.inspect_firewall(8765)
    assert result["supported"] is False


async def test_inspect_firewall_uses_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "firewall_supported", lambda: True)
    captured: dict[str, Any] = {}

    async def fake_runner(script: str, _timeout: float) -> str:
        captured["script"] = script
        return json.dumps(
            {
                "matchingRuleScopes": [{"remoteAddresses": ["Any"]}],
                "blockingRuleDetected": False,
                "privateFirewallEnabled": True,
                "networkCategory": "Private",
            }
        )

    result = await firewall.inspect_firewall(
        8765, "100.64.0.1", program_path=r"C:\swe-mux.exe", runner=fake_runner
    )
    assert result["rule_allowed"] is True
    assert result["network_category"] == "private"
    assert "100.64.0.1" in captured["script"]


async def test_inspect_firewall_unavailable_on_runner_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(firewall, "firewall_supported", lambda: True)

    async def failing_runner(_script: str, _timeout: float) -> str:
        raise RuntimeError("no powershell")

    result = await firewall.inspect_firewall(
        8765, program_path=r"C:\swe-mux.exe", runner=failing_runner
    )
    assert result["inspection_available"] is False
    assert result["supported"] is True


async def test_repair_firewall_reports_uac_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "firewall_supported", lambda: True)

    async def cancel_runner(_script: str, _timeout: float) -> str:
        return json.dumps({"launched": False, "nativeErrorCode": 1223})

    result = await firewall.repair_firewall(
        8765, program_path=r"C:\swe-mux.exe", runner=cancel_runner
    )
    assert result == {"ok": False, "reason": "cancelled"}


async def test_repair_firewall_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "firewall_supported", lambda: True)

    async def ok_runner(_script: str, _timeout: float) -> str:
        return json.dumps({"launched": True, "exitCode": 0})

    result = await firewall.repair_firewall(8765, program_path=r"C:\swe-mux.exe", runner=ok_runner)
    assert result["ok"] is True


# --------------------------------------------------------------------------- #
# Diagnostics export + firewall endpoints
# --------------------------------------------------------------------------- #


def _diagnostics_app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> web.Application:
    (tmp_path / "daemon.log").write_text("boot ok\nlistening on 8765\n", encoding="utf-8")

    async def fake_status(port: int, *, tailnet_enabled: bool = True) -> dict[str, object]:
        return {"available": True, "connection_state": "connected", "port": port}

    # Both modules that serve a route registered below reach for it: the export
    # is `routes/diagnostics.py`, the firewall pair is `routes/system.py`.
    monkeypatch.setattr(system_routes, "tailscale_status", fake_status)
    monkeypatch.setattr(diagnostics_routes, "tailscale_status", fake_status)
    app = web.Application(middlewares=[error_middleware])
    app[keys.CONFIG] = Config(data_dir=tmp_path, port=8765)
    app[keys.SESSIONS] = SimpleNamespace(sessions={})
    app[keys.NETWORK_USAGE] = SimpleNamespace(snapshot=lambda: {"totals": {}})
    app[keys.STATUS_TIMELINE] = SimpleNamespace(stats=lambda: {"rows": 0})
    app[keys.SUPERVISOR] = None
    app.router.add_get("/api/diagnostics/export", diagnostics_export)
    app.router.add_get("/api/remote/firewall", firewall_status)
    app.router.add_post("/api/remote/firewall/repair", firewall_repair)
    return app


async def test_diagnostics_export_bundles_pieces(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _diagnostics_app(tmp_path, monkeypatch)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics/export")
        assert response.status == 200
        body = await response.json()
    assert body["remote_status"]["connection_state"] == "connected"
    assert body["config"]["port"] == 8765
    assert body["logs"]["daemon"]["present"] is True
    assert "listening on 8765" in body["logs"]["daemon"]["lines"]
    assert body["logs"]["redeploy"]["present"] is False
    # Each host reports the firewall answer it actually has. This used to assert
    # `supported is False` everywhere, which was right only while Windows was the
    # sole target: POSIX now has a real answer (a reachability probe plus the
    # command this host's firewall tool would need). What must stay true on POSIX
    # is that swe-mux never claims it can *repair* anything - opening a port needs
    # root and is the user's decision.
    if sys.platform == "win32":
        # Source runs are unfrozen, so the Windows rule (bound to swe-mux.exe) is
        # correctly inert here.
        assert body["firewall"]["supported"] is False
    else:
        assert body["firewall"]["supported"] is True
        assert body["firewall"]["repair_supported"] is False
        assert body["firewall"]["needs_repair"] is False
    # The export is the sanitized public config; it must never carry a token.
    assert "token" not in body["config"]


async def test_diagnostics_export_config_is_the_public_shape(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The export must carry exactly the sanitized public config, never the raw
    # dataclass. public_dict() is the vetted no-secret shape (it has no token and
    # OpenRouter keys live in the secret store, not config).
    app = _diagnostics_app(tmp_path, monkeypatch)
    config: Config = app[keys.CONFIG]
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics/export")
        body = await response.json()
    assert set(body["config"]) == set(config.public_dict())
    assert "token" not in body["config"]


async def test_doctor_endpoint_assembles_a_report(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_status(port: int, *, tailnet_enabled: bool = True) -> dict[str, object]:
        return {
            "tailnet_enabled": True,
            "connection_state": "connected",
            "device_name": "host.ts.net",
            "connection_detail": "Connected.",
            "serve_configured": False,
        }

    monkeypatch.setattr(diagnostics_routes, "tailscale_status", fake_status)
    # Keep the wiring test hermetic: no real PATH detection or background singleton.
    # The report is assembled in `routes/diagnostics.py`, which holds its own
    # references to these - patching `routes/system.py`'s copies takes, and does
    # nothing, which showed up only as an intermittent "connected" that was the
    # host's real Tailscale answering.
    monkeypatch.setattr(diagnostics_routes, "detect_prerequisites", lambda: [])
    monkeypatch.setattr(diagnostics_routes, "detect_installations_with_versions", lambda exe: {})
    monkeypatch.setattr(
        server, "background", SimpleNamespace(health=lambda: {"degraded": [], "total_faults": 0})
    )
    app = web.Application(middlewares=[error_middleware])
    app[keys.CONFIG] = Config(data_dir=tmp_path, port=8765)
    app[keys.SESSIONS] = SimpleNamespace(sessions={}, unadopted_supervisor_sessions=0)
    app[keys.SUPERVISOR] = None
    app[keys.FRONTEND_DIR] = tmp_path
    app.router.add_get("/api/diagnostics/doctor", get_doctor_report)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/diagnostics/doctor")
        assert response.status == 200
        body = await response.json()
    assert body["version"] >= 1
    assert "checks" in body and body["checks"]
    assert body["capabilities"]["remote"]["connection_state"] == "connected"
    assert set(body["summary"]) == {"ok", "warn", "fail", "unavailable"}
    # No supervisor attached in a source run: that is a critical warn, not a leak.
    supervisor = next(c for c in body["checks"] if c["id"] == "daemon.supervisor")
    assert supervisor["status"] == "warn"


async def test_firewall_repair_requires_gesture(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _diagnostics_app(tmp_path, monkeypatch)
    async with TestClient(TestServer(app)) as client:
        missing = await client.post("/api/remote/firewall/repair")
        assert missing.status == 400
        with_gesture = await client.post(
            "/api/remote/firewall/repair", headers={"X-Mux-User-Gesture": "firewall-repair"}
        )
        # firewall_supported() is false in a source run, so the repair is refused
        # as unsupported rather than raising a UAC prompt during tests.
        assert with_gesture.status == 409
        assert (await with_gesture.json())["reason"] == "unsupported"


async def test_firewall_status_unsupported_off_frozen(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _diagnostics_app(tmp_path, monkeypatch)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/remote/firewall")
        assert response.status == 200
        assert (await response.json())["supported"] is False


# --------------------------------------------------------------------------- #
# First-use defaults and instrumentation toggles
# --------------------------------------------------------------------------- #


def test_new_user_voice_defaults_are_neutral_and_off(tmp_path: Any) -> None:
    config = Config(data_dir=tmp_path)
    # STT off so a fresh install never downloads the Whisper model unprompted.
    assert config.stt_enabled is False
    # The OS voice speaks with no download and no network call; Kokoro is a
    # deliberate choice once its pinned model is downloaded (Phase 10.5).
    assert config.tts_engine == "sapi"
    # The assistant is a model-cost feature and starts off like every other.
    assert config.assistant_enabled is False


def test_instrumentation_toggles_default_empty_and_are_restart_scoped(tmp_path: Any) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.harness_mcp_enabled == {}
    assert config.harness_instrument_enabled == {}
    hot, restart = update_config(config, {"harness_instrument_enabled": {"claude": False}})
    # Restart-scoped, because adapters are built once at daemon start.
    assert "harness_instrument_enabled" in restart
    assert "harness_instrument_enabled" not in hot
    assert load_config(path).harness_instrument_enabled == {"claude": False}


def test_instrumentation_toggles_reject_bad_values(tmp_path: Any) -> None:
    config = load_config(tmp_path / "config.toml")
    with pytest.raises(ValueError, match="unknown harnesses"):
        update_config(config, {"harness_mcp_enabled": {"ghost": True}})
    with pytest.raises(ValueError, match="harness names to booleans"):
        update_config(config, {"harness_instrument_enabled": {"claude": "no"}})


def test_claude_launch_clean_omits_hooks_but_keeps_mcp(tmp_path: Any) -> None:
    instrumented = ClaudeAdapter(data_dir=tmp_path, mcp_url="http://127.0.0.1:1/mcp")
    clean = ClaudeAdapter(data_dir=tmp_path, mcp_url="http://127.0.0.1:1/mcp", instrument=False)
    inst_argv = list(instrumented.spawn_spec("id", SpawnOptions(tmp_path)).argv)
    clean_argv = list(clean.spawn_spec("id", SpawnOptions(tmp_path)).argv)
    assert "--settings" in inst_argv
    assert "--settings" not in clean_argv
    # The MCP toggle is independent of instrumentation.
    assert "--mcp-config" in clean_argv


def test_claude_mcp_toggle_off_omits_mcp_config(tmp_path: Any) -> None:
    clean = ClaudeAdapter(data_dir=tmp_path, mcp_url="")
    argv = list(clean.spawn_spec("id", SpawnOptions(tmp_path)).argv)
    assert "--mcp-config" not in argv


def test_codex_launch_clean_omits_hooks_and_notify(tmp_path: Any) -> None:
    clean = CodexAdapter("codex.exe", notify=False, mcp_url="")
    argv = list(clean.spawn_spec("id", SpawnOptions(tmp_path)).argv)
    assert not [value for value in argv if value.startswith("hooks.")]
    assert not [value for value in argv if value.startswith("notify=")]


# --------------------------------------------------------------------------- #
# Harness registry: MCP capability + CLI version drift
# --------------------------------------------------------------------------- #


def test_registry_publishes_mcp_capability() -> None:
    payload = public_harness_registry()
    by_name = {item["name"]: item for item in payload["harnesses"]}  # type: ignore[index]
    # pi has no MCP client; the others do.
    assert by_name["pi"]["capabilities"]["mcp"] is False  # type: ignore[index]
    assert by_name["claude"]["capabilities"]["mcp"] is True  # type: ignore[index]


@pytest.mark.parametrize(
    "cli,tested,untested",
    [
        ("2.1.300", "2.1.200", True),
        ("2.1.200", "2.1.200", False),
        ("2.0.9", "2.1.0", False),
        ("garbage", "2.1.0", False),  # unparseable fails closed
        ("2.1.0", None, False),  # no bound armed
    ],
)
def test_version_is_untested(cli: str, tested: str | None, untested: bool) -> None:
    assert version_is_untested(cli, tested) is untested


def test_registry_carries_cli_version_when_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a resolved install with a known version, no real subprocess.
    monkeypatch.setattr(
        harness_module,
        "detect_installation",
        lambda name, executable=None: HarnessInstallation(
            installed=True, resolved_path="/x/claude"
        ),
    )
    monkeypatch.setattr(harness_module, "probe_cli_version", lambda name, executable=None: "9.9.9")
    monkeypatch.setattr(harness_module, "TESTED_CLI_VERSIONS", {"claude": "1.0.0"})
    installations = detect_installations_with_versions({})
    payload = public_harness_registry(installations)
    claude = next(item for item in payload["harnesses"] if item["name"] == "claude")  # type: ignore[index]
    assert claude["cli_version"] == "9.9.9"
    assert claude["version_untested"] is True


# --------------------------------------------------------------------------- #
# Onboarding prerequisites
# --------------------------------------------------------------------------- #


def test_detect_prerequisites_reports_each_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "swe_mux.prerequisites.which_real",
        lambda command: "/usr/bin/git" if command == "git" else None,
    )
    result = detect_prerequisites()
    ids = {item["id"] for item in result}
    assert {"git", "node", "npm", "tailscale"} <= ids
    git = next(item for item in result if item["id"] == "git")
    tailscale_entry = next(item for item in result if item["id"] == "tailscale")
    assert git["present"] is True
    assert git["path"] == "/usr/bin/git"
    assert tailscale_entry["present"] is False
    assert tailscale_entry["install_command"]
