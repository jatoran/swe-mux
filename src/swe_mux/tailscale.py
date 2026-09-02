from __future__ import annotations

import asyncio
import copy
import functools
import ipaddress
import json
import re
import ssl
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .bounded_subprocess import run_bounded
from .shim_paths import which_real

#: `tailscale status --json` is a few KiB per peer; 1 MiB is far above any real tailnet.
_OUTPUT_LIMIT = 1024 * 1024
#: stderr is only ever shown as a one-line diagnostic.
_STDERR_LIMIT = 64 * 1024

# Tailscale Serve terminates HTTPS on 443 and proxies to the swe-mux loopback
# port. 443 (not the swe-mux port) is required: swe-mux binds its port directly
# on the Tailscale IPv4 address for plain-HTTP fallback, so a Serve listener on
# that same port would collide with that host socket. Using 443 lets the secure
# HTTPS origin and the direct 100.x HTTP fallback coexist, and yields a clean
# port-less authority (https://<host>.ts.net/).
MOBILE_VOICE_HTTPS_PORT = 443
_WEB_URL_RE = re.compile(r"https://[^\s<>\"']+")


def _urls(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.startswith("https://"):
                found.append(key.rstrip("/"))
            found.extend(_urls(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_urls(item))
    elif isinstance(value, str) and value.startswith("https://"):
        found.append(value.rstrip("/"))
    return found


def _serve_urls(value: Any) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get("Web"), dict):
        return _urls(value)
    found: list[str] = []
    for authority in value["Web"]:
        if not isinstance(authority, str):
            continue
        candidate = authority if authority.startswith("https://") else f"https://{authority}"
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            continue
        if parsed.hostname and parsed.hostname.casefold().endswith(".ts.net"):
            found.append(candidate.rstrip("/"))
    return found


def _funnel_urls(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    found: list[str] = []
    allow = value.get("AllowFunnel")
    if isinstance(allow, dict):
        for authority, enabled in allow.items():
            if isinstance(authority, str) and enabled:
                found.append(f"https://{authority}".rstrip("/"))
    foreground = value.get("Foreground")
    if isinstance(foreground, dict):
        for config in foreground.values():
            found.extend(_funnel_urls(config))
    return found


def _targets_local_port(value: Any, port: int) -> bool:
    serialized = json.dumps(value).casefold()
    return any(
        marker in serialized
        for marker in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")
    )


def _web_target_matches(value: Any, https_port: int, target_port: int) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("Web"), dict):
        return False
    for authority, config in value["Web"].items():
        if not isinstance(authority, str):
            continue
        candidate = authority if authority.startswith("https://") else f"https://{authority}"
        if _url_on_port(candidate, https_port) and _targets_local_port(config, target_port):
            return True
    return False


def _web_target_is_loopback(value: Any, https_port: int) -> bool:
    """True if the Serve route on ``https_port`` proxies to any loopback address.

    A loopback target is a swe-mux-style private route (for example a previous
    mobile-voice setup that a different daemon left pointing at another loopback
    port). swe-mux may retarget its own such route to the running daemon's port;
    only a non-loopback route is treated as foreign and left untouched.
    """
    if not isinstance(value, dict) or not isinstance(value.get("Web"), dict):
        return False
    for authority, config in value["Web"].items():
        if not isinstance(authority, str):
            continue
        candidate = authority if authority.startswith("https://") else f"https://{authority}"
        if not _url_on_port(candidate, https_port):
            continue
        serialized = json.dumps(config).casefold()
        if any(marker in serialized for marker in ("127.0.0.1:", "localhost:", "[::1]:")):
            return True
    return False


def _loopback_target_port(value: Any, https_port: int) -> int | None:
    """The loopback port the Serve route on ``https_port`` proxies to, if any.

    Extracted rather than merely detected, because "is this loopback" is not
    enough to decide whether taking the route is safe: what matters is *which*
    daemon is on the other end and whether it is still running.
    """
    if not isinstance(value, dict) or not isinstance(value.get("Web"), dict):
        return None
    for authority, config in value["Web"].items():
        if not isinstance(authority, str):
            continue
        candidate = authority if authority.startswith("https://") else f"https://{authority}"
        if not _url_on_port(candidate, https_port):
            continue
        serialized = json.dumps(config)
        match = re.search(r"(?:127\.0\.0\.1|localhost|\[::1\]):(\d{1,5})", serialized)
        if match:
            return int(match.group(1))
    return None


async def _swemux_daemon_alive(port: int) -> bool:
    """Whether a swe-mux daemon is currently answering on this loopback port.

    `ui_build_id` rather than a bare `ok`, because the question is "is another
    swe-mux still using this route", not "is anything listening". Any failure -
    refused, timed out, wrong shape - answers False, which is the permissive
    direction on purpose: an abandoned route must stay reclaimable.
    """

    def probe() -> bool:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2
            ) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read(4096) or b"{}")
        except (OSError, ValueError, TimeoutError):
            return False
        return isinstance(payload, dict) and payload.get("ok") is True and "ui_build_id" in payload

    return await asyncio.to_thread(probe)


def _text_urls(value: str) -> list[str]:
    return [match.rstrip("/.,;)") for match in _WEB_URL_RE.findall(value)]


def _url_on_port(value: str, port: int) -> bool:
    try:
        parsed = urlsplit(value)
        effective_port = parsed.port or (443 if parsed.scheme == "https" else None)
        return parsed.scheme == "https" and effective_port == port
    except ValueError:
        return False


def mobile_voice_url(urls: list[str], port: int = MOBILE_VOICE_HTTPS_PORT) -> str | None:
    return next((url.rstrip("/") for url in urls if _url_on_port(url, port)), None)


# BackendState strings reported by `tailscale status --json`. Each maps to the
# connection state swe-mux surfaces and the exact next command a user runs to
# advance it. "Running" is the only connected state; every other real backend
# state means the tailnet listener cannot reach the phone yet.
_TS_BACKEND_STATES: dict[str, tuple[str, str | None]] = {
    "Running": ("connected", None),
    "Stopped": ("stopped", "tailscale up"),
    "Starting": ("connecting", None),
    "NeedsLogin": ("logged_out", "tailscale login"),
    "NoState": ("logged_out", "tailscale login"),
    "NeedsMachineAuth": ("needs_machine_auth", None),
}
# Shown when the Tailscale CLI is absent. winget is the documented Windows
# install path; other hosts point at the download page from the UI.
_TS_INSTALL_COMMAND = "winget install tailscale.tailscale"


def classify_tailscale_connection(available: bool, status_payload: Any) -> dict[str, object]:
    """Map raw `tailscale status --json` output to a display connection state.

    ``available`` is whether the CLI was found at all - anywhere, not only on PATH
    (`tailscale_executable`). ``status_payload`` is the parsed JSON, or ``None``
    when the status probe failed. The result reports not-installed / logged-out /
    connected-as-``<device>.ts.net`` with the exact next command per state, so the
    UI can point at the cause instead of a bare "unavailable". Unit-tested against
    real BackendState fixtures.

    The detail for the absent case used to hedge - "not installed **or is not on
    PATH**" - while the top-line state said `not_installed` flatly, and the UI
    renders the state. On every GUI Tailscale install on Windows the hedge was the
    true half and the headline was the false one. The two are now separate facts:
    resolution looks past PATH, so ``available`` false really does mean absent, and
    the caller passes ``on_path`` to say whether it can also be spawned by name.
    """
    if not available:
        return {
            "connection_state": "not_installed",
            "device_name": None,
            "connection_command": _TS_INSTALL_COMMAND,
            "connection_detail": "Tailscale is not installed on this machine.",
        }
    if not isinstance(status_payload, dict):
        return {
            "connection_state": "unknown",
            "device_name": None,
            "connection_command": "tailscale status",
            "connection_detail": "Tailscale is installed, but its status could not be read.",
        }
    self_node = status_payload.get("Self")
    dns = self_node.get("DNSName") if isinstance(self_node, dict) else None
    device_name = str(dns).strip().rstrip(".") if dns else None
    backend = str(status_payload.get("BackendState") or "").strip()
    state, command = _TS_BACKEND_STATES.get(backend, ("logged_out", "tailscale login"))
    if state == "connected":
        detail = (
            f"Connected to Tailscale as {device_name}."
            if device_name
            else "Connected to Tailscale."
        )
    elif state == "stopped":
        detail = "Tailscale is installed but stopped. Run `tailscale up` to connect."
    elif state == "connecting":
        detail = "Tailscale is connecting."
    elif state == "needs_machine_auth":
        detail = "This device is waiting for tailnet admin approval."
    else:
        detail = "Tailscale is installed but not logged in. Run `tailscale login`."
    return {
        "connection_state": state,
        "device_name": device_name,
        "connection_command": command,
        "connection_detail": detail,
    }


async def _plain_status(executable: str) -> Any | None:
    """Parse `tailscale status --json` regardless of exit code.

    The CLI prints a JSON body carrying ``BackendState`` even when it exits
    non-zero (logged out or stopped), so the state classifier must read stdout
    rather than gate on the return code.
    """
    try:
        outcome = await run_bounded(
            [executable, "status", "--json"],
            label="tailscale-status",
            timeout_seconds=4,
            output_limit=_OUTPUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
        )
    except OSError:
        return None
    if outcome.timed_out:
        return None
    try:
        return json.loads(outcome.stdout.decode("utf-8", "replace").strip() or "{}")
    except json.JSONDecodeError:
        return None


async def _status(executable: str, command: str) -> tuple[Any | None, str]:
    try:
        outcome = await run_bounded(
            [executable, command, "status", "--json"],
            label=f"tailscale-{command}-status",
            timeout_seconds=4,
            output_limit=_OUTPUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
        )
    except OSError as exc:
        return None, str(exc)
    if outcome.timed_out:
        return None, f"Tailscale {command} status command timed out."
    error = outcome.stderr.decode("utf-8", "replace").strip()
    if outcome.exit_code != 0:
        return None, error or f"Tailscale {command} is not configured."
    try:
        return json.loads(outcome.stdout.decode("utf-8", "replace").strip() or "{}"), ""
    except json.JSONDecodeError:
        return None, f"Tailscale returned unreadable {command} status."


def is_tailscale_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    if address.version == 4:
        return address in ipaddress.ip_network("100.64.0.0/10")
    return address in ipaddress.ip_network("fd7a:115c:a1e0::/48")


@functools.cache
def tailscale_executable() -> str | None:
    """The Tailscale CLI, wherever it is, or None when this host has none.

    Six call sites used to run a bare `shutil.which("tailscale")` independently,
    which made "is Tailscale here" six separate opinions, all six wrong in
    the same way: the Windows MSI installs to `C:\\Program Files\\Tailscale\\` and
    never touches PATH, so a machine with a running `tailscaled` and a healthy
    tailnet reported `not_installed` and was told to `winget install` what it
    already had. Serve, `tailscale cert`, and the direct TLS listener all silently
    became unavailable with it - which is why fixing only the classification would
    not have fixed the feature.

    Widening resolution past PATH is safe *here* and is not safe in general: this
    is not a spawn-by-name path, every caller passes the returned string as argv[0],
    and the tool being located is a fixed, known binary rather than something a
    PATH entry could shadow. `_clear_status_cache` drops it, so a re-scan sees an
    install that landed after the daemon started.
    """
    from .tool_locations import locate_tool

    return locate_tool("tailscale").path


async def tailscale_ipv4(executable: str | None = None) -> str | None:
    executable = executable or tailscale_executable()
    if not executable:
        return None
    return await _tailscale_ip(executable, "-4", label="tailscale-ip4")


async def tailscale_ipv6(executable: str | None = None) -> str | None:
    executable = executable or tailscale_executable()
    if not executable:
        return None
    return await _tailscale_ip(executable, "-6", label="tailscale-ip6")


async def _tailscale_ip(executable: str, family_flag: str, *, label: str) -> str | None:
    try:
        outcome = await run_bounded(
            [executable, "ip", family_flag],
            label=label,
            timeout_seconds=4,
            output_limit=_OUTPUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
        )
    except OSError:
        return None
    if outcome.timed_out:
        return None
    lines = outcome.stdout.decode("utf-8", "replace").strip().splitlines()
    candidate = lines[0] if lines else ""
    return candidate if outcome.exit_code == 0 and is_tailscale_ip(candidate) else None


async def tailscale_dns_name(executable: str | None = None) -> str | None:
    executable = executable or tailscale_executable()
    if not executable:
        return None
    try:
        outcome = await run_bounded(
            [executable, "status", "--json"],
            label="tailscale-dns-name",
            timeout_seconds=6,
            output_limit=_OUTPUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
        )
        if outcome.timed_out:
            return None
        payload = json.loads(outcome.stdout.decode("utf-8", "replace").strip() or "{}")
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("Self", {}).get("DNSName") if isinstance(payload, dict) else None
    return str(value).strip().rstrip(".") if value else None


async def direct_mobile_voice_tls(
    data_dir: Path, https_port: int = MOBILE_VOICE_HTTPS_PORT,
) -> tuple[list[str], str, ssl.SSLContext] | tuple[None, None, str]:
    """Prepare a direct TLS listener on the Tailscale IP without Tailscale Serve."""
    executable = tailscale_executable()
    if not executable:
        return None, None, "Tailscale CLI is not installed or is not on PATH."
    tailnet_ipv4, tailnet_ipv6, dns_name = await asyncio.gather(
        tailscale_ipv4(executable),
        tailscale_ipv6(executable),
        tailscale_dns_name(executable),
    )
    tailnet_ips = [value for value in (tailnet_ipv4, tailnet_ipv6) if value]
    if not tailnet_ips or not dns_name:
        return None, None, "Could not detect active Tailscale addresses and HTTPS name."
    cert_dir = data_dir / "tls"
    cert_dir.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    cert_path = cert_dir / f"mobile-{nonce}.crt"
    key_path = cert_dir / f"mobile-{nonce}.key"
    try:
        outcome = await run_bounded(
            [
                executable,
                "cert",
                "--min-validity=24h",
                "--cert-file",
                str(cert_path),
                "--key-file",
                str(key_path),
                dns_name,
            ],
            label="tailscale-cert",
            timeout_seconds=30,
            output_limit=_OUTPUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
        )
        if outcome.timed_out:
            return None, None, "Could not prepare direct mobile HTTPS: tailscale cert timed out."
        if outcome.exit_code != 0:
            diagnostic = (outcome.stderr or outcome.stdout).decode("utf-8", "replace").strip()
            return None, None, diagnostic or "Tailscale certificate generation failed."
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        return tailnet_ips, f"https://{dns_name}:{https_port}/", context
    except (OSError, ssl.SSLError) as exc:
        return None, None, f"Could not prepare direct mobile HTTPS: {exc}"
    finally:
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)


def listener_host_values(
    local_host: str,
    tailnet_enabled: bool,
    tailnet_ip: str | None,
    wsl_ip: str | None = None,
) -> list[str]:
    """Every address the daemon binds, loopback first.

    The WSL adapter is a third possible listener and not a variant of the tailnet
    one: it is host-local rather than remote, it is opted into separately, and it
    exists for a different reason - an agent running inside a distribution cannot
    reach a loopback-bound daemon at all, because WSL2 NAT forwards loopback into
    the distro and not back out of it.
    """
    hosts = [local_host]
    if tailnet_enabled and tailnet_ip and tailnet_ip not in hosts:
        hosts.append(tailnet_ip)
    if wsl_ip and wsl_ip not in hosts:
        hosts.append(wsl_ip)
    return hosts


async def listener_hosts(
    local_host: str, tailnet_enabled: bool, wsl_bridge_enabled: bool = False
) -> list[str]:
    wsl_ip = None
    if wsl_bridge_enabled:
        from .wsl_bridge import wsl_adapter_address

        wsl_ip = wsl_adapter_address()
    return listener_host_values(local_host, tailnet_enabled, await tailscale_ipv4(), wsl_ip)


_TS_STATUS_TTL = 15.0
_ts_status_cache: dict[tuple[int, bool], tuple[float, dict[str, object]]] = {}


def _clear_status_cache() -> None:
    _ts_status_cache.clear()
    # `getattr` because a test may have replaced the resolver with a plain
    # callable, which has no cache to drop. Clearing a cache must not be the thing
    # that raises - it runs on paths that are already recovering from something.
    getattr(tailscale_executable, "cache_clear", lambda: None)()


#: Public spelling of the same thing, for the prerequisite re-scan: a user who has
#: just added Tailscale to PATH must not keep being told for fifteen seconds, or
#: for the life of the daemon, that it is absent.
clear_status_cache = _clear_status_cache


async def tailscale_status(port: int, *, tailnet_enabled: bool = True) -> dict[str, object]:
    # Hit on every /remote/status poll; the probe spawns 3 subprocesses. Cache
    # per (port, tailnet_enabled) for a few seconds. remote_status is display
    # only, so a <=15s lag on serve/funnel/ip state is acceptable. Return a
    # deepcopy so a caller can never mutate the cached dict (tailnet_urls is a list).
    key = (port, tailnet_enabled)
    now = time.monotonic()
    cached = _ts_status_cache.get(key)
    if cached is not None and now < cached[0]:
        return copy.deepcopy(cached[1])
    result = await _probe_tailscale_status(port, tailnet_enabled=tailnet_enabled)
    _ts_status_cache[key] = (now + _TS_STATUS_TTL, result)
    return copy.deepcopy(result)


async def _probe_tailscale_status(port: int, *, tailnet_enabled: bool = True) -> dict[str, object]:
    executable = tailscale_executable()
    result: dict[str, object] = {
        "mode": "loopback",
        "listen_url": f"http://127.0.0.1:{port}",
        "available": bool(executable),
        "tailnet_enabled": tailnet_enabled,
        "tailnet_ip": None,
        "tailnet_urls": [],
        "direct_available": False,
        "serve_configured": False,
        "serve_url": None,
        "mobile_voice_configured": False,
        "mobile_voice_url": None,
        "mobile_voice_https_port": MOBILE_VOICE_HTTPS_PORT,
        "funnel_detected": False,
        "setup_command": (
            f"tailscale serve --bg --https={MOBILE_VOICE_HTTPS_PORT} "
            f"http://127.0.0.1:{port}"
        ),
        "diagnostic": "Tailscale is not installed on this machine.",
        # Where the CLI was found, so a support reader can tell an off-PATH
        # install from an on-PATH one without guessing from the state alone.
        "executable": executable,
        "on_path": bool(executable) and which_real("tailscale") is not None,
        **classify_tailscale_connection(bool(executable), None),
    }
    if not executable:
        return result
    (payload, error), (funnel, _), tailnet_ip, status_payload = await asyncio.gather(
        _status(executable, "serve"),
        _status(executable, "funnel"),
        tailscale_ipv4(executable),
        _plain_status(executable),
    )
    # The connection state (installed / logged-out / connected-as-<device>) is
    # independent of Serve configuration: a fresh install is on PATH but logged
    # out, so `available` alone cannot tell the two apart. Read it from the same
    # `tailscale status` payload that yields the DNS name.
    result.update(classify_tailscale_connection(True, status_payload))
    result["tailnet_ip"] = tailnet_ip
    result["tailnet_urls"] = [f"http://{tailnet_ip}:{port}"] if tailnet_ip else []
    result["direct_available"] = bool(tailnet_enabled and tailnet_ip)
    result["mode"] = "local+tailnet" if result["direct_available"] else "loopback"
    result["funnel_detected"] = bool(funnel and _funnel_urls(funnel))
    if payload is None:
        result["diagnostic"] = f"Could not inspect Tailscale Serve: {error}"
        return result
    urls = _serve_urls(payload)
    voice_url = mobile_voice_url(urls)
    target_ok = _targets_local_port(payload, port)
    result["serve_configured"] = bool(urls and target_ok)
    result["serve_url"] = urls[0] if urls else None
    result["mobile_voice_configured"] = bool(
        voice_url and _web_target_matches(payload, MOBILE_VOICE_HTTPS_PORT, port)
    )
    result["mobile_voice_url"] = voice_url
    result["diagnostic"] = (
        "Tailscale Serve is configured for swe-mux."
        if urls and target_ok
        else "Tailscale Serve exists, but it does not target the swe-mux loopback port."
        if urls
        else "Tailscale is available; Serve is not configured for swe-mux yet."
    )
    return result


async def enable_mobile_voice_serve(
    port: int, *, https_port: int = MOBILE_VOICE_HTTPS_PORT
) -> dict[str, object]:
    """Configure one private HTTPS listener without resetting other Serve routes."""
    executable = tailscale_executable()
    if not executable:
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": "Tailscale CLI is not installed or is not on PATH.",
        }
    funnel, _ = await _status(executable, "funnel")
    if funnel and mobile_voice_url(_funnel_urls(funnel), https_port):
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": (
                f"Port {https_port} is already exposed by Tailscale Funnel; "
                "swe-mux will not replace it."
            ),
        }
    existing, existing_error = await _status(executable, "serve")
    existing_url = mobile_voice_url(_serve_urls(existing), https_port)
    # Take over an existing route only when it already targets this daemon's port,
    # or is a swe-mux-style loopback route whose owner is gone. A non-loopback
    # route is foreign and left untouched. This lets the desktop app (8765) and a
    # terminal daemon (e.g. 18765) each reclaim the single HTTPS route when they
    # start, which is the intended behaviour and is preserved.
    #
    # What is *not* allowed is taking it from a daemon that is still running. That
    # used to happen silently and the damage is entirely on the other side of the
    # boundary: the running daemon keeps working on loopback and never learns it
    # lost the route, while every phone pointed at the tailnet URL is served by
    # whichever instance started last - and when that instance exits, the URL
    # answers nothing at all. Observed on 2026-08-17, when a short-lived test
    # daemon on an ephemeral port took the route from the live desktop app and
    # mobile access stayed broken after it exited.
    if existing_url and not _web_target_matches(existing, https_port, port):
        target_port = _loopback_target_port(existing, https_port)
        if target_port is None:
            return {
                "status": "error",
                "url": None,
                "authorization_url": None,
                "diagnostic": (
                    f"Port {https_port} already serves another private Tailscale route; "
                    "swe-mux will not replace it."
                ),
            }
        if await _swemux_daemon_alive(target_port):
            return {
                "status": "error",
                "url": None,
                "authorization_url": None,
                "diagnostic": (
                    f"Port {https_port} already serves a running swe-mux on port "
                    f"{target_port}; swe-mux will not take the address from it. Stop that "
                    "daemon first, or start this one with --local-only if it is a test "
                    "instance that should not touch the shared tailnet route."
                ),
            }
    if existing is None and existing_error and "not configured" not in existing_error.casefold():
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": f"Could not inspect Tailscale Serve: {existing_error}",
        }
    try:
        outcome = await run_bounded(
            [executable, "serve", "--bg", f"--https={https_port}", f"http://127.0.0.1:{port}"],
            label="tailscale-serve",
            timeout_seconds=30,
            output_limit=_OUTPUT_LIMIT,
            stderr_limit=_STDERR_LIMIT,
        )
    except OSError as exc:
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": f"Could not configure Tailscale Serve: {exc}",
        }
    if outcome.timed_out:
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": "Could not configure Tailscale Serve: the serve command timed out.",
        }
    output = "\n".join(
        part.decode("utf-8", "replace").strip()
        for part in (outcome.stdout, outcome.stderr)
        if part
    ).strip()
    output_urls = _text_urls(output)
    authorization_url = next(
        (url for url in output_urls if "login.tailscale.com" in url.casefold()), None
    )
    if outcome.exit_code != 0:
        return {
            "status": "authorization_required" if authorization_url else "error",
            "url": None,
            "authorization_url": authorization_url,
            "diagnostic": output or "Tailscale Serve setup failed.",
        }
    _clear_status_cache()
    payload, status_error = await _status(executable, "serve")
    configured_urls = _serve_urls(payload) if payload is not None else []
    url = mobile_voice_url([*output_urls, *configured_urls], https_port)
    if not url:
        return {
            "status": "error",
            "url": None,
            "authorization_url": authorization_url,
            "diagnostic": status_error or output or "Tailscale did not report the secure URL.",
        }
    if payload is None or not _web_target_matches(payload, https_port, port):
        return {
            "status": "error",
            "url": None,
            "authorization_url": authorization_url,
            "diagnostic": status_error or "Tailscale did not retain the swe-mux proxy target.",
        }
    return {
        "status": "ready",
        "url": f"{url}/",
        "authorization_url": authorization_url,
        "diagnostic": "Private HTTPS mobile voice address is ready.",
        "https_port": https_port,
    }
