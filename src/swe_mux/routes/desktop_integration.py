"""Desktop integration a PyPI install can turn on later (ROADMAP Phase 24).

Two halves with different difficulty, reported by one status endpoint so the
Settings group draws from a single answer:

- **Shortcuts** were always solvable and merely unwired: `shortcuts.
  apply_shortcuts` is deliberately local and idempotent, and this route runs it
  in a thread (it drives PowerShell) and returns its report verbatim.
- **The desktop shell** (tray + native window) needs `pystray` and `pywebview`
  importable by the interpreter that runs `swe_mux.desktop` - they are imported
  in-process, so no isolated-Python trick applies. `DesktopRuntimeStore`
  acquires the pinned closure on an explicit press (~2.4 MB on Windows,
  measured 2026-08-30); the status also carries the exact command that adds the
  `desktop` extra to *this* copy (`install_location.extra_install_command`) for
  anyone who prefers the install-time route, because a remedy that cannot be
  run ends the search instead of continuing it.

Windows only, by absence rather than by failure: elsewhere `supported` is false,
the frontend draws nothing, and the POSTs refuse. There is no Linux or macOS
desktop app by design (`design/features/desktop-shell.md`).
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from .. import app_keys as keys
from ..config import Config
from ..desktop_runtime import DesktopRuntimeStore, shell_importable
from ..host_platform import IS_WINDOWS
from ..http_support import json_response
from ..shortcuts import (
    ALL_SLOTS,
    SHORTCUT_FILENAME,
    apply_shortcuts,
    resolve_folders,
)

#: One store per data dir, because the download task lives on the instance and
#: two stores over one directory would be two writers on one state file.
_STORES: dict[str, DesktopRuntimeStore] = {}


def _store(config: Config) -> DesktopRuntimeStore:
    key = str(config.data_dir)
    if key not in _STORES:
        _STORES[key] = DesktopRuntimeStore(config.data_dir)
    return _STORES[key]


def _status(config: Config) -> dict[str, object]:
    if not IS_WINDOWS:
        return {"supported": False}
    from ..install_location import INSTALL_FROZEN, detect_install_location, extra_install_command

    location = detect_install_location()
    folders = resolve_folders()
    slots = {}
    for slot in ALL_SLOTS:
        path = folders.for_slot(slot) / SHORTCUT_FILENAME
        slots[slot] = {"path": str(path), "present": path.is_file()}
    return {
        "supported": True,
        "shortcuts": {"slots": slots},
        "shell": {
            "importable": shell_importable(),
            "install_kind": location.kind,
            "frozen": location.kind == INSTALL_FROZEN,
            # The install-time route, stated exactly for this install shape.
            "extra_command": extra_install_command("desktop", location),
            # The press route: the pinned-closure store's own four-state answer.
            "closure": _store(config).status(),
        },
    }


async def get_desktop_integration(request: web.Request) -> web.Response:
    config: Config = request.app[keys.CONFIG]
    return json_response(await asyncio.to_thread(_status, config))


async def post_shell_download(request: web.Request) -> web.Response:
    """Start the pinned shell-closure acquisition; idempotent while running.

    The started/already-running distinction rides on the returned status the
    same way the voice model downloads answer, so the panel needs no second
    vocabulary. The tray still needs a (re)start of the desktop app afterwards
    - the status's `importable` flips, and the frontend states the restart
    rather than letting someone discover it.
    """
    if not IS_WINDOWS:
        return json_response(
            {"error": "the desktop shell exists only on Windows; there is nothing to acquire"},
            400,
        )
    config: Config = request.app[keys.CONFIG]
    store = _store(config)
    started = store.start_download()
    return json_response({"started": started, **store.status()})


async def post_shortcuts(request: web.Request) -> web.Response:
    if not IS_WINDOWS:
        return json_response(
            {"error": "shortcuts exist only on Windows; there is nothing to write here"},
            400,
        )
    config: Config = request.app[keys.CONFIG]
    body = await request.json() if request.can_read_body else {}
    remove = bool(body.get("remove"))
    report = await asyncio.to_thread(apply_shortcuts, config=config, remove=remove)
    return json_response(report.as_dict())


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/desktop/integration", get_desktop_integration),
    web.post("/api/desktop/integration/shortcuts", post_shortcuts),
    web.post("/api/desktop/integration/shell/download", post_shell_download),
)
