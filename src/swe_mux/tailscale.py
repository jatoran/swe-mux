from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import shutil
import time
from typing import Any


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


async def _status(executable: str, command: str) -> tuple[Any | None, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            command,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=4)
    except (OSError, TimeoutError) as exc:
        return None, str(exc)
    error = stderr.decode("utf-8", "replace").strip()
    if process.returncode != 0:
        return None, error or f"Tailscale {command} is not configured."
    try:
        return json.loads(stdout.decode("utf-8", "replace").strip() or "{}"), ""
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


async def tailscale_ipv4(executable: str | None = None) -> str | None:
    executable = executable or shutil.which("tailscale")
    if not executable:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "ip",
            "-4",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=4)
    except (OSError, TimeoutError):
        return None
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    candidate = lines[0] if lines else ""
    return candidate if process.returncode == 0 and is_tailscale_ip(candidate) else None


def listener_host_values(
    local_host: str, tailnet_enabled: bool, tailnet_ip: str | None
) -> list[str]:
    hosts = [local_host]
    if tailnet_enabled and tailnet_ip:
        if tailnet_ip not in hosts:
            hosts.append(tailnet_ip)
    return hosts


async def listener_hosts(local_host: str, tailnet_enabled: bool) -> list[str]:
    return listener_host_values(local_host, tailnet_enabled, await tailscale_ipv4())


_TS_STATUS_TTL = 15.0
_ts_status_cache: dict[tuple[int, bool], tuple[float, dict[str, object]]] = {}


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
    executable = shutil.which("tailscale")
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
        "funnel_detected": False,
        "setup_command": f"tailscale serve --bg http://127.0.0.1:{port}",
        "diagnostic": "Tailscale CLI is not installed or is not on PATH.",
    }
    if not executable:
        return result
    (payload, error), (funnel, _), tailnet_ip = await asyncio.gather(
        _status(executable, "serve"),
        _status(executable, "funnel"),
        tailscale_ipv4(executable),
    )
    result["tailnet_ip"] = tailnet_ip
    result["tailnet_urls"] = [f"http://{tailnet_ip}:{port}"] if tailnet_ip else []
    result["direct_available"] = bool(tailnet_enabled and tailnet_ip)
    result["mode"] = "local+tailnet" if result["direct_available"] else "loopback"
    result["funnel_detected"] = bool(funnel and _urls(funnel))
    if payload is None:
        result["diagnostic"] = f"Could not inspect Tailscale Serve: {error}"
        return result
    serialized = json.dumps(payload).casefold()
    urls = [url for url in _urls(payload) if ".ts.net" in url.casefold()]
    target_ok = any(
        marker in serialized
        for marker in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")
    )
    result["serve_configured"] = bool(urls and target_ok)
    result["serve_url"] = urls[0] if urls else None
    result["diagnostic"] = (
        "Tailscale Serve is configured for swe-mux."
        if urls and target_ok
        else "Tailscale Serve exists, but it does not target the swe-mux loopback port."
        if urls
        else "Tailscale is available; Serve is not configured for swe-mux yet."
    )
    return result
