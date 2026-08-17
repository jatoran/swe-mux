"""The distro-side half of running an agent natively inside WSL.

A WSL profile has existed for a long time and is labelled `agent-bridge-unavailable`,
which was accurate: `wsl.exe` starts a shell inside the distribution, and everything
that makes a mux session an *observed* session lives on the Windows side of that
boundary. The agent shims are on the Windows PATH, which a Linux process does not
share. Hook commands name a Windows interpreter. Transcripts are written into the
Linux home, which no Windows path in the session record points at. So a `claude`
typed inside a WSL pane ran completely uninstrumented, and - this is the part that
made it worth fixing properly - it *looked* like a normal agent pane while doing so.

Four boundaries have to be crossed, and each is crossed by a different mechanism:

* **Executables.** Discovery must run *inside* the distribution. A Windows
  `shutil.which("claude")` finds the Windows install, or finds nothing, and neither
  answer is about the distro. Worse, a WSL PATH usually contains the Windows npm
  directory through interop, so `command -v claude` inside the distro can resolve to
  a *Windows* `.exe` that cannot serve as a native agent. `detect_distro_harnesses`
  rejects anything under `/mnt/`, which is exactly that case.

* **Paths.** `wslpath` is the authority in both directions, because it knows the
  distro's own mount table; the drive-letter regex is only a fallback for when it
  cannot be run. The Linux side of a distro is reachable from Windows as
  the wsl.localhost share, which is what lets the daemon read a transcript
  written inside the distribution without running anything.

* **The network.** This is the one that is not obvious and is why the bridge needs
  an explicit opt-in. WSL2's default NAT networking forwards `localhost` from
  Windows *into* the distro, but **not** the other way: from inside, the Windows
  host is the default gateway, not `127.0.0.1`. A hook fired by an agent in the
  distro therefore cannot reach a loopback-bound daemon at all, and would fail
  silently - the CLI runs fine and simply never reports. The daemon has to listen on
  the WSL adapter address as well, which is a real (if host-local) widening of who
  can reach it, so it is off by default and named in config rather than inferred.

* **Instrumentation.** The launcher and hook client are Python modules of a package
  that is not installed in the distribution. Installing swe-mux inside every distro
  would be the heavy answer; instead `install_bridge` materializes a single
  dependency-free stdlib script plus per-harness shims under `~/.mux-bridge/`. It
  speaks the same hook wire format as the Windows client and needs nothing but a
  Python 3 that the distro already has.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .harness import agent_harnesses, descriptor
from .host_platform import IS_WINDOWS
from .subprocess_flags import background_creation_flags

log = logging.getLogger(__name__)

# Everything here shells out to wsl.exe. Bounded, because a distribution that is
# starting up (or wedged) must degrade to "bridge unavailable" rather than block a
# spawn or a settings read.
_WSL_TIMEOUT_SECONDS = 15
_WSL_QUICK_TIMEOUT_SECONDS = 8
# Where the bridge lives inside the distribution. Under the Linux home, not under
# /mnt, so it survives the Windows side being unavailable and so its shims are on a
# native filesystem with working executable bits (a /mnt/c path cannot carry them
# reliably under the default DrvFs mount options).
BRIDGE_ROOT = PurePosixPath(".mux-bridge")


class WslBridgeError(RuntimeError):
    """The bridge could not be inspected or installed for a distribution."""


@dataclass(frozen=True)
class DistroHarness:
    """One agent CLI installed natively inside a distribution."""

    name: str
    executable: str


@dataclass(frozen=True)
class BridgeStatus:
    """What the bridge can and cannot do for one distribution, and why.

    `reasons` is deliberately part of the value rather than a log line: the whole
    failure mode this feature exists to remove is an agent that looks instrumented
    and is not, so every surface that offers a WSL profile has to be able to say
    exactly which half is missing.
    """

    distro: str
    available: bool
    harnesses: tuple[DistroHarness, ...] = ()
    linux_home: str = ""
    windows_home: str = ""
    host_address: str = ""
    installed: bool = False
    # None when it was not checked, which is different from False. A surface that
    # only lists distributions does not pay for the probe; one that is about to
    # rely on the bridge must, and must not read "not checked" as "fine".
    reachable: bool | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "distro": self.distro,
            "available": self.available,
            "harnesses": [
                {"name": item.name, "executable": item.executable} for item in self.harnesses
            ],
            "linux_home": self.linux_home,
            "windows_home": self.windows_home,
            "host_address": self.host_address,
            "installed": self.installed,
            "reachable": self.reachable,
            "reasons": list(self.reasons),
        }


_STATUS_TTL_SECONDS = 30.0
_status_cache: dict[str, tuple[float, BridgeStatus]] = {}


def cached_bridge_status(distro: str, *, daemon_port: int | None = None) -> BridgeStatus:
    """`bridge_status` with a short TTL, for surfaces that render repeatedly.

    Every field here costs at least one `wsl.exe` round trip, and the profile list
    is re-rendered on ordinary UI polls. Thirty seconds is long enough that a
    settings render is free and short enough that installing a CLI inside the
    distribution shows up while the user is still looking at the screen.
    """
    now = time.monotonic()
    key = f"{distro}:{daemon_port}"
    cached = _status_cache.get(key)
    if cached is not None and now < cached[0]:
        return cached[1]
    status = bridge_status(distro, daemon_port=daemon_port)
    _status_cache[key] = (now + _STATUS_TTL_SECONDS, status)
    return status


def clear_status_cache() -> None:
    """Drop the cached bridge status. For tests and for an explicit re-check."""
    _status_cache.clear()


def wsl_available() -> bool:
    """Whether this host can run `wsl.exe` at all."""
    return IS_WINDOWS and shutil.which("wsl.exe") is not None


def _run_in_distro(
    distro: str, command: str, *, timeout: int = _WSL_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str] | None:
    """Run one shell command inside a distribution, or None when it cannot be run.

    `-lc` (a *login* shell) matters: an agent installed under `~/.local/bin` or a
    node version manager is only on PATH after the login profile runs, so a
    non-login shell would report a perfectly working CLI as absent.
    """
    executable = shutil.which("wsl.exe")
    if not executable:
        return None
    try:
        return subprocess.run(
            [executable, "--distribution", distro, "--", "sh", "-lc", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=background_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("wsl command failed in %s: %s", distro, exc)
        return None


def to_linux_path(distro: str, windows_path: str | Path) -> str | None:
    """Translate a Windows path into the distribution's own view of it."""
    result = _run_in_distro(
        distro, f"wslpath -a -u {_sh_quote(str(windows_path))}", timeout=_WSL_QUICK_TIMEOUT_SECONDS
    )
    if result is not None and result.returncode == 0:
        translated = result.stdout.strip()
        if translated.startswith("/"):
            return translated
    return _drive_letter_fallback(str(windows_path))


def to_windows_path(distro: str, linux_path: str) -> str | None:
    """Translate a distribution path into one Windows can open.

    A path under the distro's own filesystem comes back as
    the wsl.localhost share, which is how the daemon reads a transcript
    written inside the distribution without executing anything there.
    """
    result = _run_in_distro(
        distro, f"wslpath -a -w {_sh_quote(linux_path)}", timeout=_WSL_QUICK_TIMEOUT_SECONDS
    )
    if result is not None and result.returncode == 0:
        translated = result.stdout.strip()
        if translated:
            return translated
    if linux_path.startswith("/"):
        return rf"\wsl.localhost\{distro}{linux_path.replace('/', chr(92))}"
    return None


def _drive_letter_fallback(windows_path: str) -> str | None:
    """`C:\\x` -> `/mnt/c/x`, for when `wslpath` could not be run.

    Only correct for a default `/mnt` automount, which is why it is a fallback and
    not the primary: a distro with a custom `automount.root` would be mistranslated,
    and `wslpath` reads the real mount table.
    """
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", windows_path)
    if not match:
        return None
    return f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}"


def distro_home(distro: str) -> str | None:
    """The distribution's own `$HOME`, as a Linux path."""
    result = _run_in_distro(distro, "printf %s \"$HOME\"", timeout=_WSL_QUICK_TIMEOUT_SECONDS)
    if result is None or result.returncode != 0:
        return None
    home = result.stdout.strip()
    return home if home.startswith("/") else None


def host_address(distro: str) -> str | None:
    """The address the Windows host answers on, seen from inside the distribution.

    Under WSL2's default NAT networking that is the default gateway, and it is *not*
    `127.0.0.1`: loopback is forwarded Windows-into-distro but not distro-to-Windows.
    Under mirrored networking the host really is reachable on loopback, and the
    gateway lookup returns nothing, so loopback is the right answer there - the two
    cases are distinguished by what the routing table says rather than by asking WSL
    which mode it is in.
    """
    # Read the kernel's routing table and parse it here rather than piping through
    # `awk` inside the distro. Two reasons, and the first was measured: a shell
    # pipeline has to survive Python -> wsl.exe -> `sh -lc` argument handling, and
    # the `awk '{print $3}'` form silently came back unfiltered across that boundary,
    # which read as "no gateway" and quietly fell back to loopback - the exact wrong
    # answer, since it is unreachable. Second, `/proc/net/route` needs no external
    # command at all, so a minimal distro with no `iproute2` still answers.
    result = _run_in_distro(
        distro, "cat /proc/net/route", timeout=_WSL_QUICK_TIMEOUT_SECONDS
    )
    if result is not None and result.returncode == 0:
        gateway = _default_gateway_from_proc(result.stdout)
        if gateway:
            return gateway
    # Mirrored networking, or a routing table we could not read. Loopback is
    # correct in the first case and harmless in the second, where the reachability
    # check below is what actually decides.
    return "127.0.0.1"


def _default_gateway_from_proc(table: str) -> str | None:
    """The default route's gateway from `/proc/net/route` contents.

    The kernel writes each address as little-endian hex, so `016011AC` is
    `172.17.96.1` read backwards a byte at a time. Pure, so the parsing is testable
    without a distribution - which matters because getting the byte order wrong
    produces a plausible-looking address that simply is not the host.
    """
    for line in table.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            raw = int(fields[2], 16)
        except ValueError:
            continue
        if raw == 0:
            continue
        octets = [(raw >> shift) & 0xFF for shift in (0, 8, 16, 24)]
        return ".".join(str(octet) for octet in octets)
    return None


def wsl_adapter_subnet() -> str | None:
    """The WSL virtual subnet in CIDR form, for scoping a firewall rule to it."""
    if not IS_WINDOWS:
        return None
    try:
        import ipaddress

        import psutil

        for name, addresses in psutil.net_if_addrs().items():
            if "wsl" not in name.casefold():
                continue
            for address in addresses:
                value = getattr(address, "address", "") or ""
                netmask = getattr(address, "netmask", "") or ""
                if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value) or not netmask:
                    continue
                network = ipaddress.ip_network(f"{value}/{netmask}", strict=False)
                return str(network)
    except (OSError, ValueError, ImportError) as exc:
        log.debug("could not derive the WSL subnet: %s", exc)
    return None


def daemon_reachable(distro: str, address: str, port: int) -> bool:
    """Whether the distribution can actually open a TCP connection to the daemon.

    Asked rather than assumed, because the failure it detects is invisible from
    the Windows side: Defender Firewall drops inbound connections on the WSL
    adapter by default, so the listener binds fine, Windows itself reaches it, and
    only the distribution times out. An agent then starts perfectly and never
    reports - the exact silent-uninstrumented state the bridge exists to remove.
    A *timeout* rather than a refusal is the signature, which is why this probes
    instead of inspecting rules.
    """
    script = (
        "python3 -c \"import socket,sys;"
        f"s=socket.socket();s.settimeout(4);sys.exit(s.connect_ex(('{address}',{port})))\" "
        "&& echo reachable"
    )
    result = _run_in_distro(distro, script, timeout=_WSL_QUICK_TIMEOUT_SECONDS)
    return result is not None and "reachable" in (result.stdout or "")


def wsl_adapter_address() -> str | None:
    """The Windows-side address of the WSL NAT network, or None when there is none.

    This is the address a bridged distribution reaches the daemon on, and it is the
    *same* address the distro sees as its default gateway - the two ends of one
    virtual link. Finding it from the Windows side matters because that is where the
    daemon binds, and a distribution that is not running cannot be asked.

    Returns None under mirrored networking, where there is no separate adapter and
    the daemon's ordinary loopback listener is already reachable from inside.
    """
    if not IS_WINDOWS:
        return None
    try:
        import psutil

        for name, addresses in psutil.net_if_addrs().items():
            if "wsl" not in name.casefold():
                continue
            for address in addresses:
                value = getattr(address, "address", "") or ""
                if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value):
                    return value
    except (OSError, ValueError, ImportError) as exc:
        log.debug("could not enumerate the WSL adapter: %s", exc)
    return None


def detect_distro_harnesses(distro: str) -> tuple[DistroHarness, ...]:
    """Agent CLIs installed *natively* in the distribution.

    Anything resolving under `/mnt/` is rejected. A WSL PATH normally includes the
    Windows npm directory through interop, so `command -v codex` frequently resolves
    to `/mnt/c/Users/.../npm/codex` - a Windows binary. It runs, which is what makes
    this dangerous: accepting it would produce a session that looks bridged while the
    agent writes its transcript into the Windows home and its hooks name a Windows
    interpreter, which is precisely the uninstrumented state the bridge exists to end.
    """
    found: list[DistroHarness] = []
    for name in agent_harnesses():
        command = descriptor(name).script_base_name
        result = _run_in_distro(
            distro,
            f"command -v {_sh_quote(command)} 2>/dev/null",
            timeout=_WSL_QUICK_TIMEOUT_SECONDS,
        )
        if result is None or result.returncode != 0:
            continue
        resolved = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if not resolved.startswith("/"):
            continue
        if resolved.startswith("/mnt/"):
            log.debug(
                "%s in %s resolves to a Windows binary through interop (%s); not a native agent",
                command,
                distro,
                resolved,
            )
            continue
        found.append(DistroHarness(name, resolved))
    return tuple(found)


def bridge_status(distro: str, *, daemon_port: int | None = None) -> BridgeStatus:
    """Everything a caller needs to decide whether to offer a bridged WSL profile.

    Pass `daemon_port` to include the reachability check. It costs one command
    inside the distribution and it is the only way to catch the firewall drop,
    so every surface that is about to *rely* on the bridge should pass it; a
    surface that is only listing distributions need not.
    """
    if not wsl_available():
        return BridgeStatus(distro, False, reasons=("wsl.exe is not available on this host",))
    reasons: list[str] = []
    home = distro_home(distro)
    if not home:
        return BridgeStatus(
            distro, False, reasons=(f"could not read $HOME inside {distro}; is it running?",)
        )
    harnesses = detect_distro_harnesses(distro)
    if not harnesses:
        reasons.append(
            f"no agent CLI is installed natively inside {distro} "
            "(a Windows CLI reached through /mnt does not count)"
        )
    python = _distro_python(distro)
    if not python:
        reasons.append(f"no python3 inside {distro}, which the bridge's hook client needs")
    installed = _bridge_installed(distro, home)
    windows_home = to_windows_path(distro, home) or ""
    address = host_address(distro) or ""
    reachable: bool | None = None
    if daemon_port is not None and address:
        reachable = daemon_reachable(distro, address, daemon_port)
        if not reachable:
            subnet = wsl_adapter_subnet() or "the WSL subnet"
            reasons.append(
                f"{distro} cannot reach the daemon at {address}:{daemon_port}. The daemon "
                "must listen on the WSL adapter (set wsl_bridge_enabled) and Windows "
                f"Defender Firewall must allow inbound TCP {daemon_port} from {subnet}; "
                "without both, an agent in the distribution runs but its hooks never arrive"
            )
    return BridgeStatus(
        distro=distro,
        available=bool(harnesses and python and reachable is not False),
        harnesses=harnesses,
        linux_home=home,
        windows_home=windows_home,
        host_address=address,
        installed=installed,
        reachable=reachable,
        reasons=tuple(reasons),
    )


def _distro_python(distro: str) -> str | None:
    result = _run_in_distro(
        distro,
        "command -v python3 2>/dev/null || command -v python 2>/dev/null",
        timeout=_WSL_QUICK_TIMEOUT_SECONDS,
    )
    if result is None or result.returncode != 0:
        return None
    resolved = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
    return resolved if resolved.startswith("/") and not resolved.startswith("/mnt/") else None


def _bridge_installed(distro: str, home: str) -> bool:
    marker = f"{home}/{BRIDGE_ROOT}/mux_bridge.py"
    result = _run_in_distro(
        distro, f"test -f {_sh_quote(marker)} && echo yes", timeout=_WSL_QUICK_TIMEOUT_SECONDS
    )
    return result is not None and result.stdout.strip() == "yes"


def install_bridge(distro: str) -> BridgeStatus:
    """Materialize the distro-side bridge, then report the resulting status.

    Written through the distribution rather than through the wsl.localhost share, because
    the shims need an executable bit and a Windows-side write cannot set one.
    """
    status = bridge_status(distro)
    if not status.linux_home:
        raise WslBridgeError(f"could not read $HOME inside {distro}")
    python = _distro_python(distro)
    if not python:
        raise WslBridgeError(f"no python3 inside {distro}; the bridge hook client needs one")
    root = f"{status.linux_home}/{BRIDGE_ROOT}"
    payload = _bridge_source()
    # Delivered on stdin and written by the distro's own shell. Passing a
    # multi-hundred-line script as an argument would hit command-line limits and
    # would need two levels of quoting to survive `sh -lc`.
    script = (
        f"set -e; mkdir -p {_sh_quote(root)}/bin; "
        f"cat > {_sh_quote(root)}/mux_bridge.py; "
        f"chmod 0644 {_sh_quote(root)}/mux_bridge.py"
    )
    executable = shutil.which("wsl.exe")
    if not executable:
        raise WslBridgeError("wsl.exe is not available")
    try:
        result = subprocess.run(
            [executable, "--distribution", distro, "--", "sh", "-lc", script],
            input=payload,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_WSL_TIMEOUT_SECONDS,
            creationflags=background_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslBridgeError(f"could not write the bridge into {distro}: {exc}") from exc
    if result.returncode != 0:
        raise WslBridgeError(f"could not write the bridge into {distro}: {result.stderr.strip()}")
    for harness in status.harnesses:
        _write_distro_shim(distro, root, python, harness.name)
    return bridge_status(distro)


def _write_distro_shim(distro: str, root: str, python: str, harness: str) -> None:
    shim = f"{root}/bin/{harness}"
    body = (
        "#!/bin/sh\n"
        "# swe_mux.agent_launcher\n"
        f'exec {python} {root}/mux_bridge.py {harness} "$@"\n'
    )
    script = f"cat > {_sh_quote(shim)} && chmod 0755 {_sh_quote(shim)}"
    executable = shutil.which("wsl.exe")
    if not executable:
        return
    try:
        subprocess.run(
            [executable, "--distribution", distro, "--", "sh", "-lc", script],
            input=body,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_WSL_TIMEOUT_SECONDS,
            creationflags=background_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not write the %s bridge shim in %s: %s", harness, distro, exc)


def bridge_environment(
    status: BridgeStatus, *, hook_url: str, hook_secret: str, session_id: str
) -> dict[str, str]:
    """The environment a bridged WSL session hands to the distribution.

    `MUX_BRIDGE_HOOK_URL` is rewritten to the address that resolves *from inside*,
    because the daemon's own ingress URL names a loopback host the distro cannot
    reach under NAT networking.
    """
    env = {
        "MUX_BRIDGE_HOOK_URL": rewrite_ingress_for_distro(hook_url, status.host_address),
        "MUX_BRIDGE_HOOK_SECRET": hook_secret,
        "MUX_BRIDGE_SESSION_ID": session_id,
        "MUX_SHIM_DIR": f"{status.linux_home}/{BRIDGE_ROOT}/bin",
    }
    for harness in status.harnesses:
        prefix = f"MUX_{harness.name.upper().replace('-', '_')}"
        env[f"{prefix}_EXE"] = harness.executable
    return env


def rewrite_ingress_for_distro(url: str, address: str) -> str:
    """Point a loopback ingress URL at the address the distribution can reach.

    Only a loopback host is rewritten. A daemon already reachable on a routable
    address (a tailnet URL) is left alone, because that address works from inside
    the distribution too and second-guessing it would break the case that already
    worked.
    """
    if not address:
        return url
    return re.sub(r"//(127\.0\.0\.1|localhost|\[::1\])(?=[:/]|$)", f"//{address}", url, count=1)


def _bridge_source() -> str:
    """The dependency-free distro-side script.

    Kept as one stdlib file on purpose. Installing swe-mux into every distribution
    would drag a dependency tree, a Python version requirement, and an upgrade
    problem across a boundary that only needs two things done: run the real CLI,
    and POST a hook payload back to the daemon.
    """
    return _BRIDGE_SOURCE


_BRIDGE_SOURCE = '''#!/usr/bin/env python3
"""swe-mux WSL bridge: run a native Linux agent CLI and report its hooks home.

Materialized into a distribution by swe_mux.wsl_bridge. Standard library only, so
it works on whatever python3 the distro already has and needs no install.

It does two jobs:

  * launch  - exec the real CLI named by MUX_<HARNESS>_EXE, forwarding argv exactly
  * hook    - POST a hook payload to the daemon, using the address that resolves
              from inside the distribution rather than the daemon's loopback one
"""

import json
import os
import sys
import urllib.error
import urllib.request

HOOK_TIMEOUT_SECONDS = 10


def _hook_url():
    return os.environ.get("MUX_BRIDGE_HOOK_URL", "").strip()


def _post_hook(event, payload):
    url = _hook_url()
    secret = os.environ.get("MUX_BRIDGE_HOOK_SECRET", "")
    if not url or not secret:
        return False
    body = json.dumps({"event": event, "payload": payload}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Mux-Hook-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HOOK_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        # A hook that cannot be delivered must never take the agent down with it.
        return False


def _launch(harness, args):
    variable = "MUX_" + harness.upper().replace("-", "_") + "_EXE"
    executable = os.environ.get(variable, "").strip()
    if not executable:
        sys.stderr.write(
            "swe-mux bridge: %s is not set; cannot find the %s CLI inside this distribution\\n"
            % (variable, harness)
        )
        return 127
    if not os.path.isabs(executable) or executable.startswith("/mnt/"):
        sys.stderr.write(
            "swe-mux bridge: %s points at %s, which is not a native Linux executable\\n"
            % (variable, executable)
        )
        return 127
    try:
        os.execv(executable, [executable] + list(args))
    except OSError as exc:
        sys.stderr.write("swe-mux bridge: could not exec %s: %s\\n" % (executable, exc))
        return 127
    return 127


def main(argv):
    if not argv:
        sys.stderr.write("swe-mux bridge: no harness named\\n")
        return 2
    if argv[0] == "--hook":
        if len(argv) < 2:
            return 2
        try:
            payload = json.load(sys.stdin)
        except (ValueError, OSError):
            payload = {}
        return 0 if _post_hook(argv[1], payload) else 1
    return _launch(argv[0], argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def _sh_quote(value: str) -> str:
    """POSIX single-quote a value for `sh -lc`, since `shlex.quote` targets the host."""
    return "'" + value.replace("'", "'\\''") + "'"


__all__ = [
    "BRIDGE_ROOT",
    "cached_bridge_status",
    "clear_status_cache",
    "daemon_reachable",
    "wsl_adapter_address",
    "wsl_adapter_subnet",
    "BridgeStatus",
    "DistroHarness",
    "WslBridgeError",
    "bridge_environment",
    "bridge_status",
    "detect_distro_harnesses",
    "distro_home",
    "host_address",
    "install_bridge",
    "rewrite_ingress_for_distro",
    "to_linux_path",
    "to_windows_path",
    "wsl_available",
]
