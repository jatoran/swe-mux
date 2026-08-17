"""The POSIX half of "can a peer actually reach this daemon", behind the same gate.

`windows_firewall.py` inspects Defender rules and offers an elevated repair,
because on Windows the rule *is* the thing that decides. POSIX is not the mirror
image of that and this module deliberately does not pretend it is:

* There is no single firewall to inspect. A host may run `ufw`, `firewalld`,
  plain `nftables`, `iptables`, none of them, or a cloud security group that is
  not on the host at all. Reading whichever one happens to be installed would
  produce a confident answer that is wrong on the next machine.
* Editing rules needs root. Prompting for it from a daemon that never otherwise
  asks would be a poor trade for a diagnostic, and `sudo` in a background service
  has no interactive path anyway.

So the POSIX answer is the one the cross-platform findings call for: **probe
reachability, and if it fails, name the tool this host actually has and the exact
command to run.** A probe is also strictly better evidence than a rule read - it
answers the question the user has ("can my phone reach this?") rather than a proxy
for it, and it stays correct when the blocker is upstream of the host entirely.

Nothing here mutates. `repair_supported` is False on POSIX and says why.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket

from .host_platform import IS_LINUX, IS_MACOS, IS_POSIX

log = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 3.0


def posix_firewall_supported() -> bool:
    """Whether this module has anything to say about the host."""
    return IS_POSIX


async def probe_reachable(host: str, port: int) -> bool:
    """Whether a TCP connection to ``host:port`` completes from this machine.

    A loopback probe proves the listener exists; the caller supplies a routable
    address when it wants to know whether a *peer* could reach it. Both are useful
    and they answer different questions, so the address is the caller's choice.
    """
    try:
        future = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(future, timeout=_PROBE_TIMEOUT_SECONDS)
    except (TimeoutError, OSError, socket.gaierror):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, ConnectionError):
        pass
    return True


def firewall_tool() -> str | None:
    """Which host firewall front-end is installed, if any.

    Ordered by how specific the advice can be, not alphabetically: `ufw` and
    `firewalld` own their rules and reject hand-written `nft` changes, so if either
    is present it is the right thing to name even when `nft` is also installed.
    """
    if IS_MACOS:
        # The application firewall is off by default and does not block inbound
        # TCP to a listening socket the way Defender does, so there is usually
        # nothing to advise. Named anyway so the caller can say so explicitly.
        return "socketfilterfw" if shutil.which("socketfilterfw") else None
    if not IS_LINUX:
        return None
    for tool in ("ufw", "firewall-cmd", "nft", "iptables"):
        if shutil.which(tool):
            return tool
    return None


def allow_command(port: int) -> str | None:
    """The exact command that would open ``port`` on this host, or None.

    Returned as advice for the user to run, never executed: opening a port is a
    security decision and it needs root, so a daemon that quietly did it would be
    both overstepping and unable to.
    """
    tool = firewall_tool()
    if tool == "ufw":
        return f"sudo ufw allow {port}/tcp"
    if tool == "firewall-cmd":
        return (
            f"sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload"
        )
    if tool == "nft":
        return (
            "sudo nft add rule inet filter input tcp dport "
            f"{port} accept"
        )
    if tool == "iptables":
        return f"sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT"
    return None


async def inspect_posix_firewall(port: int, address: str | None = None) -> dict[str, object]:
    """A status payload shaped like the Windows one, so callers stay platform-free.

    `needs_repair` stays False even when the probe fails, because this module
    cannot repair anything and the field means "swe-mux can fix this for you". The
    actionable part is `remedy`.
    """
    if not posix_firewall_supported():
        return {"supported": False, "inspection_available": False, "needs_repair": False}
    target = address or "127.0.0.1"
    reachable = await probe_reachable(target, port)
    tool = firewall_tool()
    remedy = None
    if not reachable:
        remedy = allow_command(port) or (
            "no host firewall tool was found; the block may be upstream of this "
            "machine (a cloud security group, a router, or a container network)"
        )
    return {
        "supported": True,
        "inspection_available": True,
        "port": port,
        "address": target,
        "reachable": reachable,
        "firewall_tool": tool,
        # Deliberately never True: repairing needs root and is the user's decision.
        "needs_repair": False,
        "repair_supported": False,
        "remedy": remedy,
        "detail": (
            f"{target}:{port} is reachable from this host."
            if reachable
            else f"{target}:{port} could not be reached from this host."
        ),
    }


__all__ = [
    "allow_command",
    "firewall_tool",
    "inspect_posix_firewall",
    "posix_firewall_supported",
    "probe_reachable",
]
