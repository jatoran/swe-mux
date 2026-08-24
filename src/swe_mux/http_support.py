"""Transport primitives any module that touches an HTTP response may use.

Separate from `routes/support.py`, which resolves a request to the Project or
session it names: these four know nothing about swe-mux's domains, and modules
below the route layer (`preview_transport`) need them too. A module there
reaching up into `routes/` would invert the dependency direction the package
boundary exists to state.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any

from aiohttp import web

from .network_usage import (
    compact_json_response,
)

log = logging.getLogger(__name__)


def json_response(data: Any, status: int = 200) -> web.Response:
    return compact_json_response(data, status=status)


def log_task_failure(task: asyncio.Task[Any]) -> None:
    """Surface a one-shot background task's death instead of swallowing it."""
    if task.cancelled():
        return
    if (error := task.exception()) is not None:
        log.error("background task %s failed", task.get_name(), exc_info=error)


def is_loopback_peer(value: str) -> bool:
    peer = value.split("%", 1)[0]
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def apply_security_headers(response: web.StreamResponse, request: web.Request) -> None:
    """Stamp response security headers.

    Shared by the security middleware and the preview passthrough, which streams
    its own StreamResponse and so must set these before it calls prepare() (the
    middleware's post-handler stamping would otherwise be too late).
    """
    if request.path.startswith("/preview/"):
        csp = (
            "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; "
            "connect-src * data: blob:; frame-ancestors 'self'"
        )
    else:
        csp = (
            # 'wasm-unsafe-eval' permits the note editor's local WebAssembly
            # compilation without allowing general eval or any network access.
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
            "connect-src 'self' ws: wss:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "frame-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # microphone=(self) keeps third-party frames blocked while allowing the app's
    # own dictation (STT) feature to request the microphone on secure contexts.
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(self), geolocation=()"
    )
    if not request.path.startswith("/preview/"):
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
