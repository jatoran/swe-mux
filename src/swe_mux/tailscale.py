from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import re
import shutil
import ssl
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .subprocess_flags import background_creation_flags

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


async def _status(executable: str, command: str) -> tuple[Any | None, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            command,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
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
            creationflags=background_creation_flags(),
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=4)
    except (OSError, TimeoutError):
        return None
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    candidate = lines[0] if lines else ""
    return candidate if process.returncode == 0 and is_tailscale_ip(candidate) else None


async def tailscale_ipv6(executable: str | None = None) -> str | None:
    executable = executable or shutil.which("tailscale")
    if not executable:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "ip",
            "-6",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=4)
    except (OSError, TimeoutError):
        return None
    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    candidate = lines[0] if lines else ""
    return candidate if process.returncode == 0 and is_tailscale_ip(candidate) else None


async def tailscale_dns_name(executable: str | None = None) -> str | None:
    executable = executable or shutil.which("tailscale")
    if not executable:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=6)
        payload = json.loads(stdout.decode("utf-8", "replace").strip() or "{}")
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None
    value = payload.get("Self", {}).get("DNSName") if isinstance(payload, dict) else None
    return str(value).strip().rstrip(".") if value else None


async def direct_mobile_voice_tls(
    data_dir: Path, https_port: int = MOBILE_VOICE_HTTPS_PORT,
) -> tuple[list[str], str, ssl.SSLContext] | tuple[None, None, str]:
    """Prepare a direct TLS listener on the Tailscale IP without Tailscale Serve."""
    executable = shutil.which("tailscale")
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
        process = await asyncio.create_subprocess_exec(
            executable,
            "cert",
            "--min-validity=24h",
            "--cert-file",
            str(cert_path),
            "--key-file",
            str(key_path),
            dns_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode != 0:
            diagnostic = (stderr or stdout).decode("utf-8", "replace").strip()
            return None, None, diagnostic or "Tailscale certificate generation failed."
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        return tailnet_ips, f"https://{dns_name}:{https_port}/", context
    except (OSError, TimeoutError, ssl.SSLError) as exc:
        return None, None, f"Could not prepare direct mobile HTTPS: {exc}"
    finally:
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)


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


def _clear_status_cache() -> None:
    _ts_status_cache.clear()


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
        "mobile_voice_configured": False,
        "mobile_voice_url": None,
        "mobile_voice_https_port": MOBILE_VOICE_HTTPS_PORT,
        "funnel_detected": False,
        "setup_command": (
            f"tailscale serve --bg --https={MOBILE_VOICE_HTTPS_PORT} "
            f"http://127.0.0.1:{port}"
        ),
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
    executable = shutil.which("tailscale")
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
    # Take over an existing route only when it already targets this daemon's port
    # or is a swe-mux-style loopback route (e.g. another daemon on a different port
    # left it pointing at 127.0.0.1:<other>). A non-loopback route is foreign and
    # left untouched. This lets the desktop app (8765) and a terminal daemon
    # (e.g. 18765) each reclaim the single HTTPS route when they start.
    if (
        existing_url
        and not _web_target_matches(existing, https_port, port)
        and not _web_target_is_loopback(existing, https_port)
    ):
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": (
                f"Port {https_port} already serves another private Tailscale route; "
                "swe-mux will not replace it."
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
        process = await asyncio.create_subprocess_exec(
            executable,
            "serve",
            "--bg",
            f"--https={https_port}",
            f"http://127.0.0.1:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except (OSError, TimeoutError) as exc:
        return {
            "status": "error",
            "url": None,
            "authorization_url": None,
            "diagnostic": f"Could not configure Tailscale Serve: {exc}",
        }
    output = "\n".join(
        part.decode("utf-8", "replace").strip() for part in (stdout, stderr) if part
    ).strip()
    output_urls = _text_urls(output)
    authorization_url = next(
        (url for url in output_urls if "login.tailscale.com" in url.casefold()), None
    )
    if process.returncode != 0:
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
