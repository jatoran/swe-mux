"""Desktop integration a PyPI install can turn on later (ROADMAP Phase 24).

One status endpoint and one write, so the Settings group draws from a single
answer:

- **Shortcuts** were always solvable and merely unwired: `shortcuts.
  apply_shortcuts` is deliberately local and idempotent, and this route runs it
  in a thread (it drives PowerShell) and returns its report verbatim. The
  request names which slots it wants, including `startup` - see below.
- **The desktop shell** (tray + native window) needs `pystray` and `pywebview`
  importable by the interpreter that runs `swe_mux.desktop`. Since 2026-08-30
  they are base dependencies, so the honest answer here is a yes or a no about
  *this* environment rather than an offer: the status reports importability and,
  when something is missing, the reinstall command for this install shape.

**What this endpoint used to do, and why it stopped.** Until the dependency move
it also carried a `shell/download` action that acquired a pinned ~2.4 MB wheel
closure into the data dir, because an install could legitimately lack the tray.
That was a repair for a packaging decision, and the packaging decision was the
thing worth changing - `pyproject.toml` carries the argument. A press that can
only ever report "already installed" is not a capability, so it went with the
closure it fetched.

**The `startup` slot is the reason the request takes slots at all.** The status
has always reported all three, and the write hard-coded Start Menu and Desktop -
so this surface could *remove* a run-at-login entry and never create one, while
the only way to turn it on was the tray menu (unreachable from a phone or any
remote client) or a CLI flag. Naming the slots closes that.

Windows only, by absence rather than by failure: elsewhere `supported` is false,
the frontend draws nothing, and the POSTs refuse. There is no Linux or macOS
desktop app by design (`design/features/desktop-shell.md`).

**Session-link hand-offs** (`desktop_links.py`) live here too, because they are the same
question - is the desktop app on this machine? - asked by a browser tab that a session link
opened. The daemon answers it rather than the page, for the one reason the page cannot: it
knows whether the tab is on *this* machine. A tab on a phone or another PC reaches the daemon
through Tailscale Serve, which names the real client in `X-Forwarded-For`, and only a client
that is this host itself is told to try ``swe-mux://``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging

import psutil
from aiohttp import web

from .. import app_keys as keys
from ..config import Config
from ..desktop_links import (
    LinkHandoff,
    LinkHandoffRegistry,
    SessionLink,
    link_handler_installed,
    session_link_url,
    valid_handoff_id,
    valid_session_id,
)
from ..desktop_runtime import missing_shell_modules
from ..host_platform import IS_WINDOWS
from ..http_support import json_response
from ..network_usage import request_peer
from ..shortcuts import (
    ALL_SLOTS,
    SHORTCUT_FILENAME,
    ShortcutError,
    apply_shortcuts,
    resolve_folders,
)
from ..ui_messages import error_text, ui_message

log = logging.getLogger(__name__)


def _status(config: Config) -> dict[str, object]:
    if not IS_WINDOWS:
        return {"supported": False}
    from ..install_location import INSTALL_FROZEN, detect_install_location

    location = detect_install_location()
    folders = resolve_folders()
    slots = {}
    for slot in ALL_SLOTS:
        path = folders.for_slot(slot) / SHORTCUT_FILENAME
        slots[slot] = {"path": str(path), "present": path.is_file()}
    missing = missing_shell_modules()
    from ..desktop import _run_key_enabled

    return {
        "supported": True,
        "shortcuts": {"slots": slots},
        "startup_enabled": _run_key_enabled(config) or slots["startup"]["present"],
        "shell": {
            "importable": not missing,
            "missing": list(missing),
            "install_kind": location.kind,
            "frozen": location.kind == INSTALL_FROZEN,
            # Empty when nothing is wrong, and empty for a frozen bundle even
            # when something is - there is no installer there to re-run.
            "reinstall_command": location.reinstall_command() if missing else "",
        },
    }


async def get_desktop_integration(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    return json_response(await asyncio.to_thread(_status, config))


def _requested_slots(body: dict[str, object]) -> tuple[str, ...] | None:
    """The slots a write asks for, validated, or None to mean "the default".

    An absent `slots` key keeps the endpoint's original behaviour rather than
    writing nothing, because a client from before this field existed is a client
    that means "the usual two".
    """
    raw = body.get("slots")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ShortcutError("`slots` must be a list of slot names")
    unknown = sorted({item for item in raw if item not in ALL_SLOTS})
    if unknown:
        raise ShortcutError(
            f"unknown shortcut slot(s): {', '.join(unknown)}; "
            f"expected any of {', '.join(ALL_SLOTS)}"
        )
    return tuple(dict.fromkeys(raw))


async def post_shortcuts(request: web.Request) -> web.Response:
    if not IS_WINDOWS:
        return json_response(
            {"error": ui_message("desktop_integration.00c6d5588f")},
            400,
        )
    config: Config = request.app[keys.CONFIG]
    body = await request.json() if request.can_read_body else {}
    remove = bool(body.get("remove"))
    try:
        slots = _requested_slots(body)
    except ShortcutError as exc:
        return json_response({"error": error_text(exc)}, 400)
    # `apply_shortcuts` addresses every slot on removal by design, so passing the
    # request's selection there would narrow a cleanup that must not be narrowed -
    # and an absent `slots` must fall through to that function's own default
    # rather than to an empty tuple, which is why this branches instead of
    # forwarding a computed value.
    if slots is None or remove:
        report = await asyncio.to_thread(apply_shortcuts, config=config, remove=remove)
    else:
        report = await asyncio.to_thread(apply_shortcuts, config=config, remove=remove, slots=slots)
    return json_response(report.as_dict())


def local_interface_addresses() -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address this host's own interfaces carry, including its Tailscale one."""
    found: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for addresses in psutil.net_if_addrs().values():
        for address in addresses:
            try:
                found.add(ipaddress.ip_address(str(address.address).split("%", 1)[0]))
            except ValueError:
                continue
    return frozenset(found)


def peer_is_this_host(
    peer: str,
    addresses: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> bool:
    """Whether a request's client is this machine: loopback, or one of its own addresses.

    `peer` is `network_usage.request_peer`'s answer, which already prefers the forwarded
    client when the direct peer is a local proxy. A browser on this PC opening the Tailscale
    URL arrives as this host's own tailnet address (measured 2026-11-08), so it counts; a
    phone or another PC on the tailnet arrives as its own address and does not.
    """
    try:
        address = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address in addresses


def handoff_refusal(peer: str) -> str | None:
    """Why a browser tab must not try ``swe-mux://``, or None when it should.

    Ordered from cheapest to read: platform, then device, then the registry. Runs on a
    worker thread because the last two touch the OS.
    """
    if not IS_WINDOWS:
        return "unsupported"
    if not peer_is_this_host(peer, local_interface_addresses()):
        return "other_device"
    if not link_handler_installed():
        return "not_installed"
    return None


def _handoffs(request: web.Request) -> LinkHandoffRegistry:
    return request.app[keys.DESKTOP_LINK_HANDOFFS]


async def post_link_handoff(request: web.Request) -> web.Response:
    """Start a hand-off for a browser tab a session link opened, if one can succeed.

    Always 200 with `attempt`: "do not try" is the ordinary answer for a phone, a Linux box
    or a machine without the app, and the tab simply stays the web UI.
    """
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    session_id = body.get("session_id") if isinstance(body, dict) else None
    if not valid_session_id(session_id):
        return json_response({"error": "session_id is required"}, 400)
    assert isinstance(session_id, str)
    peer = request_peer(request)
    refusal = await asyncio.to_thread(handoff_refusal, peer)
    if refusal is not None:
        log.info(
            "desktop link handoff not attempted",
            extra={"link_session": session_id, "link_reason": refusal, "link_peer": peer},
        )
        return json_response({"attempt": False, "reason": refusal})
    handoff: LinkHandoff = _handoffs(request).create(session_id)
    url = session_link_url(SessionLink(session_id=session_id, handoff=handoff.handoff_id))
    log.info(
        "desktop link handoff started",
        extra={"link_session": session_id, "link_handoff": handoff.handoff_id, "link_peer": peer},
    )
    return json_response({"attempt": True, "handoff_id": handoff.handoff_id, "url": url})


async def get_link_handoff(request: web.Request) -> web.Response:
    handoff_id = request.match_info["handoff_id"]
    handoff = _handoffs(request).get(handoff_id) if valid_handoff_id(handoff_id) else None
    if handoff is None:
        return json_response({"state": "unknown"}, 404)
    return json_response(handoff.as_dict())


async def post_link_handoff_opened(request: web.Request) -> web.Response:
    """The desktop page took the link: the browser tab that asked may close.

    Sent by whichever page loaded with the hand-off id, which only the desktop window is
    ever given. The id is unguessable, so nothing else can close someone's tab with it,
    and the worst a forged report could do is exactly that.
    """
    handoff_id = request.match_info["handoff_id"]
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    found = bool(body.get("found")) if isinstance(body, dict) else False
    handoff = (
        _handoffs(request).mark_opened(handoff_id, found=found)
        if valid_handoff_id(handoff_id)
        else None
    )
    if handoff is None:
        log.info(
            "desktop link handoff report for an unknown id", extra={"link_handoff": handoff_id[:64]}
        )
        return json_response({"state": "unknown"}, 404)
    log.info(
        "desktop link handoff opened",
        extra={
            "link_session": handoff.session_id,
            "link_handoff": handoff.handoff_id,
            "link_found": handoff.found,
        },
    )
    return json_response(handoff.as_dict())


def note_expired_handoff(handoff: LinkHandoff) -> None:
    """The record a hand-off the desktop app never took leaves behind."""
    if handoff.opened_at is None:
        log.info(
            "desktop link handoff expired unopened",
            extra={"link_session": handoff.session_id, "link_handoff": handoff.handoff_id},
        )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/desktop/integration", get_desktop_integration),
    web.post("/api/desktop/integration/shortcuts", post_shortcuts),
    web.post("/api/desktop/link-handoffs", post_link_handoff),
    web.get("/api/desktop/link-handoffs/{handoff_id}", get_link_handoff),
    web.post("/api/desktop/link-handoffs/{handoff_id}/opened", post_link_handoff_opened),
)
