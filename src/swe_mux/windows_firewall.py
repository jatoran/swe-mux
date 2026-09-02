"""Windows Defender Firewall inspect and repair for the tailnet listener.

swe-mux binds a real host socket on its ``100.x`` Tailscale IPv4 address, so on
Windows the Defender Firewall governs inbound connections to ``swe-mux.exe`` on
the Private profile. A blocking or absent inbound rule silently stops the first
phone connect while the desktop keeps working over loopback, and nothing reports
why. This module detects that state and offers a one-click elevated repair.

Everything here sits behind a platform boundary: :func:`firewall_supported` is
the single gate, and it is false off Windows and off a frozen build. On a
headless Linux host the equivalent is a reachability probe plus ``ufw`` /
``firewalld`` guidance, which lives elsewhere (see CROSS_PLATFORM_FINDINGS.md).

The PowerShell mechanics mirror the Orca reference
(``windows-mobile-firewall.ts``): NetSecurity filter properties are stable
across localized Windows, and the ``ActiveStore`` policy store includes
GPO-applied rules the default persistent store hides, so a managed Block rule
cannot produce a false success. The scope check is swe-mux-specific: the phone
connects from an unknown address inside the tailnet, so a sufficient Allow rule
must cover the whole ``100.64.0.0/10`` range, never just this desktop.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import PureWindowsPath
from typing import Any

from .bounded_subprocess import run_bounded

#: The inspection script prints one JSON document describing a handful of rules.
_POWERSHELL_OUTPUT_LIMIT = 1024 * 1024
#: stderr is only ever surfaced as the failure message.
_POWERSHELL_STDERR_LIMIT = 64 * 1024

# The tailnet range every phone address falls inside. A firewall Allow rule is
# only sufficient if its remote-address scope covers this whole network.
TAILNET_IPV4 = ipaddress.ip_network("100.64.0.0/10")

FIREWALL_RULE_NAME = "swe-mux Mobile"
FIREWALL_RULE_DISPLAY_NAME = "swe-mux Mobile Access"
FIREWALL_RULE_DESCRIPTION = (
    "Allows a phone on this tailnet to reach swe-mux on private networks."
)

# The WSL bridge needs its own rule, and for the same reason the tailnet one
# exists: swe-mux binds a real host socket on the WSL virtual adapter, and
# Defender governs inbound connections to it. Measured on this host - a listener
# on the WSL adapter is reachable from Windows and *times out* from inside the
# distribution, which is the signature of a DROP rather than a missing listener.
# Without the rule an agent in the distro runs perfectly and its hooks never
# arrive, which is the silent-uninstrumented state the bridge exists to end.
#
# A separate rule rather than widening the mobile one: the two have different
# scopes (a host-local virtual subnet versus the tailnet range), and a user who
# enables the bridge has not thereby consented to phone access, or the reverse.
WSL_FIREWALL_RULE_NAME = "swe-mux WSL Bridge"
WSL_FIREWALL_RULE_DISPLAY_NAME = "swe-mux WSL Bridge Access"
WSL_FIREWALL_RULE_DESCRIPTION = (
    "Allows an agent running inside a WSL distribution to reach swe-mux hooks on this host."
)

# Inspection is a read; repair launches an elevated shell and waits for the UAC
# prompt plus the rule change, so it needs a far longer ceiling.
POWERSHELL_TIMEOUT_SECONDS = 10.0
ELEVATION_TIMEOUT_SECONDS = 5 * 60.0

# Windows native error code raised when the user declines the UAC prompt.
_UAC_CANCELLED_ERROR = 1223

PowerShellRunner = Callable[[str, float], Awaitable[str]]


def firewall_supported() -> bool:
    """True only on a frozen Windows build, where the rule targets swe-mux.exe.

    Source/dev runs execute from ``python.exe``; a firewall rule bound to that
    transient interpreter path is meaningless and must never be created, so the
    whole feature is inert unless this is the packaged executable.
    """
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def firewall_program_path() -> str:
    """The executable the firewall rule applies to: the running swe-mux.exe."""
    return sys.executable


def _parse_ipv4_range(scope: str) -> tuple[int, int] | None:
    """Parse a Windows remote-address scope to an inclusive IPv4 integer range.

    Handles the forms ``Get-NetFirewallAddressFilter`` reports: a single host, a
    CIDR (``10.0.0.0/24``), a dotted-netmask CIDR (``10.0.0.0/255.255.255.0``),
    and a dash range (``10.0.0.1-10.0.0.5``). Non-IPv4 and malformed scopes
    return ``None`` so the caller fails closed.
    """
    text = scope.strip()
    if not text:
        return None
    if "-" in text:
        start_text, _, end_text = text.partition("-")
        try:
            start = ipaddress.IPv4Address(start_text.strip())
            end = ipaddress.IPv4Address(end_text.strip())
        except ipaddress.AddressValueError:
            return None
        return (int(start), int(end)) if int(start) <= int(end) else None
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None
    if network.version != 4:
        return None
    return int(network.network_address), int(network.broadcast_address)


def scope_covers_tailnet(scope: Any) -> bool:
    """True if a firewall Allow scope reaches the phone across the tailnet.

    The phone connects from an unknown address inside ``100.64.0.0/10``, so a
    sufficient scope must span that whole range or be an unrestricted IPv4
    keyword. ``LocalSubnet`` (a single-host tailnet interface is a ``/32``) and a
    desktop-only host both fail closed: they would let this desktop reach itself
    but never admit the phone.
    """
    if not isinstance(scope, str):
        return False
    keyword = scope.strip().casefold()
    if keyword in {"any", "any4"}:
        return True
    if keyword in {"any6", "localsubnet", "localsubnet4", "localsubnet6"}:
        return False
    parsed = _parse_ipv4_range(scope)
    if parsed is None:
        return False
    start, end = parsed
    return start <= int(TAILNET_IPV4.network_address) and end >= int(
        TAILNET_IPV4.broadcast_address
    )


def _has_sufficient_scope(matching_rule_scopes: Any) -> bool:
    # Coverage is checked per rule, never unioned across rules: this advisory
    # check fails safe, so accepting fragmented rules would only add risk.
    rules = matching_rule_scopes if isinstance(matching_rule_scopes, list) else [matching_rule_scopes]
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        remote = rule.get("remoteAddresses")
        addresses = remote if isinstance(remote, list) else [remote]
        if any(scope_covers_tailnet(address) for address in addresses):
            return True
    return False


def _network_category(value: Any) -> str:
    return {
        "Private": "private",
        "Public": "public",
        "DomainAuthenticated": "domain",
    }.get(str(value), "unknown")


def _inspection_detail(
    *, blocking: bool, direct_path_blocked: bool, private_enabled: bool, serve_active: bool
) -> str:
    # When Tailscale Serve is up, the phone reaches swe-mux over the loopback proxy
    # (tailscaled terminates TLS on 443 and forwards to 127.0.0.1:<port>), which
    # Windows Firewall never governs. The inbound rule only affects the direct
    # 100.x HTTP fallback, so a missing or blocking rule is not a phone-connect
    # failure here, only a latent issue for that fallback.
    if serve_active:
        if blocking:
            return (
                "Phone access works over the secure Tailscale Serve address (a loopback "
                "proxy the firewall does not govern). A firewall rule blocks the direct "
                "100.x HTTP fallback; repair it only if you use that fallback."
            )
        if direct_path_blocked:
            return (
                "Phone access works over the secure Tailscale Serve address (a loopback "
                "proxy the firewall does not govern). The direct 100.x HTTP fallback has no "
                "inbound Allow rule; repair it only if you use that fallback."
            )
        return (
            "Phone access works over the secure Tailscale Serve address, and the direct "
            "100.x HTTP fallback is also allowed."
        )
    if blocking:
        return (
            "A Windows Defender Firewall rule blocks inbound connections to swe-mux on "
            "private networks, and Tailscale Serve is not up to route the phone over "
            "loopback, so a phone using the direct 100.x address cannot connect. Repair "
            "removes the block and adds a scoped Allow rule."
        )
    if not private_enabled:
        return (
            "The private firewall profile is disabled, so inbound connections are already "
            "allowed. No firewall rule is needed."
        )
    if direct_path_blocked:
        return (
            "Tailscale Serve is not up, so the phone would use the direct 100.x address, "
            "but no inbound firewall rule admits it, so the connect will silently fail. "
            "Repair adds a scoped Allow rule (or set up the secure mobile address instead)."
        )
    return "Windows Defender Firewall allows inbound connections to swe-mux on private networks."


def interpret_inspection(
    port: int, program_path: str, parsed: Any, *, serve_active: bool = False
) -> dict[str, object]:
    """Turn parsed PowerShell inspection output into the display status.

    ``serve_active`` is whether Tailscale Serve proxies this port over loopback. When
    it does, the inbound firewall rule is irrelevant to phone connectivity (the
    phone arrives via loopback), so a missing or blocking rule is reported without
    raising the repair alarm. Pure and platform-independent, so the block/allow/
    scope logic is unit-tested against fixtures without a Windows host.
    """
    if not isinstance(parsed, dict):
        return _unavailable_status(port, program_path, serve_active=serve_active)
    blocking = parsed.get("blockingRuleDetected") is True
    private_enabled = parsed.get("privateFirewallEnabled") is not False
    has_scope = _has_sufficient_scope(parsed.get("matchingRuleScopes"))
    # The direct 100.x inbound path is blocked when a Block rule exists, or the
    # private profile is on with no scope-sufficient Allow rule.
    direct_path_blocked = blocking or (private_enabled and not has_scope)
    # But that only stops a phone connect when Serve is not routing over loopback.
    needs_repair = direct_path_blocked and not serve_active
    return {
        "supported": True,
        "inspection_available": True,
        "port": port,
        "program": program_path,
        "serve_active": serve_active,
        "rule_allowed": not blocking and has_scope,
        "blocking_rule_detected": blocking,
        "direct_path_blocked": direct_path_blocked,
        "needs_repair": needs_repair,
        "private_firewall_enabled": private_enabled,
        "network_category": _network_category(parsed.get("networkCategory")),
        "detail": _inspection_detail(
            blocking=blocking,
            direct_path_blocked=direct_path_blocked,
            private_enabled=private_enabled,
            serve_active=serve_active,
        ),
    }


def _unsupported_status() -> dict[str, object]:
    return {"supported": False, "inspection_available": False, "needs_repair": False}


def _unavailable_status(
    port: int, program_path: str, *, serve_active: bool = False
) -> dict[str, object]:
    # Firewall inspection is advisory; an unavailable PowerShell or a managed
    # policy must not nag or hide the explicit repair option.
    return {
        "supported": True,
        "inspection_available": False,
        "port": port,
        "program": program_path,
        "serve_active": serve_active,
        "rule_allowed": False,
        "blocking_rule_detected": False,
        "direct_path_blocked": False,
        "needs_repair": False,
        "private_firewall_enabled": True,
        "network_category": "unknown",
        "detail": (
            "swe-mux could not inspect Windows Defender Firewall on this machine. "
            "You can still run the repair to add an inbound Allow rule for the direct "
            "100.x fallback."
        ),
    }


def _quote_ps(value: str) -> str:
    """Single-quote a value for PowerShell, escaping embedded single quotes."""
    return "'" + value.replace("'", "''") + "'"


def build_inspection_script(port: int, program_path: str, address: str | None = None) -> str:
    # NetSecurity filter properties are stable across localized Windows display
    # output and keep every rule's address scope independent. ActiveStore
    # includes GPO-applied rules the persistent store hides.
    address_lookup = ""
    if address:
        address_lookup = f"""
try {{
  $ip = Get-NetIPAddress -IPAddress {_quote_ps(address)} -ErrorAction Stop | Select-Object -First 1
  $profileInfo = Get-NetConnectionProfile -InterfaceIndex $ip.InterfaceIndex -ErrorAction Stop | Select-Object -First 1
  if ($profileInfo) {{ $networkCategory = [string]$profileInfo.NetworkCategory }}
}} catch {{}}"""
    return f"""$ErrorActionPreference = 'Stop'
$matchingRuleScopes = @()
$blockingRuleDetected = $false
$rules = @(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore -Program {_quote_ps(program_path)} -ErrorAction SilentlyContinue | Get-NetFirewallRule | Where-Object {{ $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' }})
foreach ($rule in $rules) {{
  $portFilter = $rule | Get-NetFirewallPortFilter
  $protocol = [string]$portFilter.Protocol
  $ruleProfile = [string]$rule.Profile
  $portMatches = @($portFilter.LocalPort | Where-Object {{ [string]$_ -eq 'Any' -or [string]$_ -eq '{port}' }}).Count -gt 0
  if (($protocol -eq 'Any' -or $protocol -eq 'TCP' -or $protocol -eq '6') -and ($ruleProfile -eq 'Any' -or $ruleProfile -match 'Private') -and $portMatches) {{
    if ([string]$rule.Action -eq 'Block') {{
      $blockingRuleDetected = $true
    }} elseif ([string]$rule.Action -eq 'Allow') {{
      $addressFilter = $rule | Get-NetFirewallAddressFilter
      $matchingRuleScopes += [pscustomobject]@{{ remoteAddresses = @($addressFilter.RemoteAddress | ForEach-Object {{ [string]$_ }}) }}
    }}
  }}
}}
$privateFirewallEnabled = [bool](Get-NetFirewallProfile -PolicyStore ActiveStore -Name Private).Enabled
$networkCategory = 'Unknown'{address_lookup}
[pscustomobject]@{{
  matchingRuleScopes = @($matchingRuleScopes)
  blockingRuleDetected = $blockingRuleDetected
  privateFirewallEnabled = $privateFirewallEnabled
  networkCategory = $networkCategory
}} | ConvertTo-Json -Depth 4 -Compress"""


def build_repair_script(port: int, program_path: str) -> str:
    # Windows gives explicit Block rules precedence over narrower Allow rules, so
    # the repair removes exact-app inbound conflicts first, then adds one scoped
    # Allow rule. Block removal ignores the block's remote scope, mirroring the
    # fail-closed inspection (the phone address is unknown).
    return f"""$ErrorActionPreference = 'Stop'
$blockingRules = @(Get-NetFirewallApplicationFilter -Program {_quote_ps(program_path)} -ErrorAction SilentlyContinue | Get-NetFirewallRule | Where-Object {{ $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' }})
foreach ($rule in $blockingRules) {{
  $portFilter = $rule | Get-NetFirewallPortFilter
  $protocol = [string]$portFilter.Protocol
  $ruleProfile = [string]$rule.Profile
  $portMatches = @($portFilter.LocalPort | Where-Object {{ [string]$_ -eq 'Any' -or [string]$_ -eq '{port}' }}).Count -gt 0
  if (($protocol -eq 'Any' -or $protocol -eq 'TCP' -or $protocol -eq '6') -and ($ruleProfile -eq 'Any' -or $ruleProfile -match 'Private') -and $portMatches) {{
    $rule | Remove-NetFirewallRule
  }}
}}
Get-NetFirewallRule -Name {_quote_ps(FIREWALL_RULE_NAME)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name {_quote_ps(FIREWALL_RULE_NAME)} -DisplayName {_quote_ps(FIREWALL_RULE_DISPLAY_NAME)} -Description {_quote_ps(FIREWALL_RULE_DESCRIPTION)} -Direction Inbound -Action Allow -Enabled True -Profile Private -Protocol TCP -LocalPort {port} -Program {_quote_ps(program_path)} -EdgeTraversalPolicy Block | Out-Null"""


def build_wsl_repair_script(port: int, program_path: str, remote_subnet: str) -> str:
    """One inbound Allow rule scoped to the WSL virtual subnet.

    Scoped to `remote_subnet` rather than Any: the only peers that need to reach
    this socket are distributions on this machine, and the WSL adapter's own
    subnet names exactly those. Widening it to Any would make the bridge's opt-in
    quietly grant more than it says.
    """
    return f"""$ErrorActionPreference = 'Stop'
Get-NetFirewallRule -Name {_quote_ps(WSL_FIREWALL_RULE_NAME)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name {_quote_ps(WSL_FIREWALL_RULE_NAME)} -DisplayName {_quote_ps(WSL_FIREWALL_RULE_DISPLAY_NAME)} -Description {_quote_ps(WSL_FIREWALL_RULE_DESCRIPTION)} -Direction Inbound -Action Allow -Enabled True -Profile Any -Protocol TCP -LocalPort {port} -RemoteAddress {_quote_ps(remote_subnet)} -Program {_quote_ps(program_path)} -EdgeTraversalPolicy Block | Out-Null"""


def build_elevation_script(powershell_path: str, encoded_repair_script: str) -> str:
    # Start-Process -Verb RunAs raises the single UAC prompt; NativeErrorCode
    # 1223 is the user declining it, which the caller reports as "cancelled".
    return f"""$ErrorActionPreference = 'Stop'
try {{
  $process = Start-Process -FilePath {_quote_ps(powershell_path)} -ArgumentList @('-NoProfile', '-NonInteractive', '-EncodedCommand', '{encoded_repair_script}') -Verb RunAs -Wait -PassThru
  [pscustomobject]@{{ launched = $true; exitCode = $process.ExitCode }} | ConvertTo-Json -Compress
}} catch {{
  [pscustomobject]@{{ launched = $false; nativeErrorCode = $_.Exception.NativeErrorCode }} | ConvertTo-Json -Compress
}}"""


def _encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _windows_powershell_path() -> str:
    # Windows PowerShell 5.1 always ships the NetSecurity module and exists on
    # every supported Windows; PowerShell 7 (pwsh) may be absent.
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(
        PureWindowsPath(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )


async def _run_powershell(script: str, timeout: float) -> str:  # noqa: ASYNC109
    powershell = _windows_powershell_path()
    encoded = _encode_powershell(script)
    outcome = await run_bounded(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        label="powershell-firewall",
        timeout_seconds=timeout,
        output_limit=_POWERSHELL_OUTPUT_LIMIT,
        stderr_limit=_POWERSHELL_STDERR_LIMIT,
    )
    if outcome.timed_out:
        raise TimeoutError("PowerShell timed out")
    if outcome.exit_code != 0:
        message = outcome.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "PowerShell exited non-zero")
    return outcome.stdout.decode("utf-8", "replace")


async def inspect_firewall(
    port: int | None,
    address: str | None = None,
    *,
    serve_active: bool = False,
    program_path: str | None = None,
    runner: PowerShellRunner | None = None,
    timeout: float = POWERSHELL_TIMEOUT_SECONDS,  # noqa: ASYNC109
) -> dict[str, object]:
    """Report whether Defender Firewall admits phone connections to swe-mux.

    ``serve_active`` should be true when Tailscale Serve proxies this port over
    loopback, so a missing or blocking inbound rule is not flagged as a phone-
    connect failure (the phone arrives via loopback, which the firewall never
    governs; the rule only affects the direct 100.x HTTP fallback).
    """
    if not firewall_supported() or port is None:
        return _unsupported_status()
    program = program_path or firewall_program_path()
    run = runner or _run_powershell
    try:
        raw = await run(build_inspection_script(port, program, address), timeout)
        parsed = json.loads(raw.strip())
    except (OSError, TimeoutError, ValueError, RuntimeError):
        return _unavailable_status(port, program, serve_active=serve_active)
    return interpret_inspection(port, program, parsed, serve_active=serve_active)


async def repair_wsl_firewall(
    port: int | None,
    remote_subnet: str | None,
    *,
    program_path: str | None = None,
    runner: PowerShellRunner | None = None,
    timeout: float = ELEVATION_TIMEOUT_SECONDS,  # noqa: ASYNC109
) -> dict[str, object]:
    """Add the inbound Allow rule a bridged WSL agent needs (elevated).

    Deliberately the same shape and the same elevation path as `repair_firewall`,
    down to the return contract, so the two panels behave identically and a caller
    cannot learn one and be surprised by the other.

    `remote_subnet` is required rather than defaulted: the rule must be scoped to
    the WSL adapter's own subnet, and inventing a scope when it cannot be read
    would silently widen it to something the user never agreed to.
    """
    if not firewall_supported() or port is None:
        return {"ok": False, "reason": "unsupported"}
    if not remote_subnet:
        return {"ok": False, "reason": "no_wsl_adapter"}
    program = program_path or firewall_program_path()
    run = runner or _run_powershell
    inner = build_wsl_repair_script(port, program, remote_subnet)
    outer = build_elevation_script(_windows_powershell_path(), _encode_powershell(inner))
    try:
        raw = await run(outer, timeout)
        result = json.loads(raw.strip())
    except (OSError, TimeoutError, ValueError, RuntimeError):
        return {"ok": False, "reason": "failed"}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "failed"}
    if not result.get("launched") and result.get("nativeErrorCode") == _UAC_CANCELLED_ERROR:
        return {"ok": False, "reason": "cancelled"}
    if result.get("launched") and result.get("exitCode") == 0:
        return {"ok": True, "reason": None}
    return {"ok": False, "reason": "failed"}


async def repair_firewall(
    port: int | None,
    *,
    program_path: str | None = None,
    runner: PowerShellRunner | None = None,
    timeout: float = ELEVATION_TIMEOUT_SECONDS,  # noqa: ASYNC109
) -> dict[str, object]:
    """Remove blocking rules and add one scoped inbound Allow rule (elevated)."""
    if not firewall_supported() or port is None:
        return {"ok": False, "reason": "unsupported"}
    program = program_path or firewall_program_path()
    run = runner or _run_powershell
    inner = build_repair_script(port, program)
    outer = build_elevation_script(_windows_powershell_path(), _encode_powershell(inner))
    try:
        raw = await run(outer, timeout)
        result = json.loads(raw.strip())
    except (OSError, TimeoutError, ValueError, RuntimeError):
        return {"ok": False, "reason": "failed"}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "failed"}
    if not result.get("launched") and result.get("nativeErrorCode") == _UAC_CANCELLED_ERROR:
        return {"ok": False, "reason": "cancelled"}
    if result.get("launched") and result.get("exitCode") == 0:
        return {"ok": True, "reason": None}
    return {"ok": False, "reason": "failed"}
