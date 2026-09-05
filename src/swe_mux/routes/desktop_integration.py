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
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from .. import app_keys as keys
from ..config import Config
from ..desktop_runtime import missing_shell_modules
from ..host_platform import IS_WINDOWS
from ..http_support import json_response
from ..shortcuts import (
    ALL_SLOTS,
    SHORTCUT_FILENAME,
    ShortcutError,
    apply_shortcuts,
    resolve_folders,
)


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
            {"error": "shortcuts exist only on Windows; there is nothing to write here"},
            400,
        )
    config: Config = request.app[keys.CONFIG]
    body = await request.json() if request.can_read_body else {}
    remove = bool(body.get("remove"))
    try:
        slots = _requested_slots(body)
    except ShortcutError as exc:
        return json_response({"error": str(exc)}, 400)
    # `apply_shortcuts` addresses every slot on removal by design, so passing the
    # request's selection there would narrow a cleanup that must not be narrowed -
    # and an absent `slots` must fall through to that function's own default
    # rather than to an empty tuple, which is why this branches instead of
    # forwarding a computed value.
    if slots is None or remove:
        report = await asyncio.to_thread(apply_shortcuts, config=config, remove=remove)
    else:
        report = await asyncio.to_thread(
            apply_shortcuts, config=config, remove=remove, slots=slots
        )
    return json_response(report.as_dict())


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/desktop/integration", get_desktop_integration),
    web.post("/api/desktop/integration/shortcuts", post_shortcuts),
)
