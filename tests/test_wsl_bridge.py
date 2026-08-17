"""The WSL bridge's decision logic, tested without needing a distribution running.

The parts that must touch a real distro (discovery, installation, reachability)
are proven by the live run recorded in ROADMAP Phase 10; what is here is
everything that can be got wrong *without* a distro and would then be wrong
everywhere: address rewriting, routing-table parsing, and the capability label.

The label is the one that matters most. Its whole job is to stop a WSL pane from
presenting an uninstrumented agent as an observed one, so the test asserts that
"not checked" and "checked and unusable" both read as unavailable.
"""

from __future__ import annotations

import sys

import pytest

from swe_mux.config import LaunchProfile
from swe_mux.profiles import derive_capabilities, wsl_distro_of
from swe_mux.wsl_bridge import _default_gateway_from_proc, rewrite_ingress_for_distro

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="WSL is a Windows host feature")


def _wsl_profile(distro: str = "Ubuntu") -> LaunchProfile:
    return LaunchProfile(
        f"wsl-{distro.casefold()}",
        f"WSL: {distro}",
        "wsl.exe",
        ["--distribution", distro],
        cwd_strategy="wsl",
        marker="wsl",
    )


def test_a_loopback_ingress_is_rewritten_to_the_address_the_distro_can_reach() -> None:
    """WSL2 NAT forwards loopback Windows-into-distro, never back out.

    An agent inside the distribution therefore cannot reach a loopback hook URL at
    all. It fails silently - the CLI runs fine and simply never reports - which is
    why the rewrite is not optional.
    """
    for host in ("127.0.0.1", "localhost", "[::1]"):
        rewritten = rewrite_ingress_for_distro(f"http://{host}:8765/api/hooks/sid", "172.17.96.1")
        assert rewritten == "http://172.17.96.1:8765/api/hooks/sid"


def test_an_already_routable_ingress_is_left_alone() -> None:
    """A tailnet URL works from inside the distribution too; rewriting it would break it."""
    url = "http://100.64.1.2:8765/api/hooks/sid"
    assert rewrite_ingress_for_distro(url, "172.17.96.1") == url
    # No address to rewrite to is not a reason to mangle the URL.
    assert rewrite_ingress_for_distro(url, "") == url


def test_the_default_gateway_is_read_from_the_kernel_routing_table() -> None:
    """`/proc/net/route` writes addresses little-endian; reading them forwards is wrong.

    A byte-order mistake here produces a plausible-looking address that is simply
    not the host, so the daemon would be unreachable in a way that looks like a
    firewall problem. This is the exact table shape measured on the test host.
    """
    table = (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t016011AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t006011AC\t00000000\t0001\t0\t0\t0\t00F0FFFF\t0\t0\t0\n"
    )
    assert _default_gateway_from_proc(table) == "172.17.96.1"


def test_a_routing_table_with_no_default_route_yields_nothing() -> None:
    """Mirrored networking has no gateway, and inventing one would be worse than none."""
    table = (
        "Iface\tDestination\tGateway \tFlags\n"
        "eth0\t006011AC\t00000000\t0001\n"
    )
    assert _default_gateway_from_proc(table) is None
    assert _default_gateway_from_proc("") is None


def test_a_wsl_profile_names_its_own_distribution() -> None:
    assert wsl_distro_of(_wsl_profile("Ubuntu")) == "Ubuntu"
    assert wsl_distro_of(LaunchProfile("x", "X", "bash", [])) is None


def test_an_unverified_wsl_profile_never_claims_a_bridge() -> None:
    """"Not checked" must read the same as "checked and unusable".

    Reporting a bridge nobody verified would present an uninstrumented agent as an
    observed one, which is the single failure this label exists to prevent - and it
    is invisible, because the pane looks completely normal while producing no hooks,
    no transcript link, and no status.
    """
    profile = _wsl_profile()
    for unverified in (None, False):
        capabilities = derive_capabilities(
            profile, breakpoints=True, wsl_bridge_ready=unverified
        )
        assert "agent-bridge-unavailable" in capabilities
        assert "agent-bridge" not in capabilities
        # A WSL pane is never agent-aware in the Windows-shim sense, bridged or not.
        assert "agent-aware" not in capabilities


def test_a_verified_wsl_profile_reports_the_bridge() -> None:
    capabilities = derive_capabilities(_wsl_profile(), breakpoints=True, wsl_bridge_ready=True)
    assert "agent-bridge" in capabilities
    assert "agent-bridge-unavailable" not in capabilities
    assert "wsl" in capabilities


@WINDOWS_ONLY
def test_the_listener_set_includes_the_wsl_adapter_only_when_opted_in() -> None:
    """Binding the WSL adapter widens who can reach a daemon that has no login.

    Every process in every distribution on the machine can reach it, so it is opted
    into explicitly rather than inferred from "this host has WSL".
    """
    from swe_mux.tailscale import listener_host_values

    assert listener_host_values("127.0.0.1", False, None, None) == ["127.0.0.1"]
    assert listener_host_values("127.0.0.1", False, None, "172.17.96.1") == [
        "127.0.0.1",
        "172.17.96.1",
    ]
    # The tailnet and WSL listeners are independent, and loopback stays first
    # because it is the one the daemon cannot run without.
    assert listener_host_values("127.0.0.1", True, "100.64.1.2", "172.17.96.1") == [
        "127.0.0.1",
        "100.64.1.2",
        "172.17.96.1",
    ]


@WINDOWS_ONLY
def test_the_wsl_firewall_rule_is_scoped_to_the_wsl_subnet() -> None:
    """Scoped, not Any: only distributions on this machine need to reach the socket."""
    from swe_mux.windows_firewall import WSL_FIREWALL_RULE_NAME, build_wsl_repair_script

    script = build_wsl_repair_script(8765, r"C:\\app\\swe-mux.exe", "172.17.96.0/20")
    assert WSL_FIREWALL_RULE_NAME in script
    assert "-RemoteAddress '172.17.96.0/20'" in script
    assert "-LocalPort 8765" in script
    assert "-Direction Inbound" in script
    assert "-Action Allow" in script
    # It must never be written as an unscoped allow.
    assert "-RemoteAddress 'Any'" not in script
