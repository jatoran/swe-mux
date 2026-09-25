"""Where a loopback preview opens, and how its pane learns the page changed.

The regression these pin (2026-09-25): a preview was keyed by host and port, and
the listener scan usually registered it at `/` before anyone clicked the link an
agent printed. The click then found the existing registration and dropped the path,
so a preview of `http://127.0.0.1:8766/cart-drawings.html` opened as the directory
listing every time. When the click won the race instead, the path became the proxy
base, which rebased every root-relative asset and turned `/page.html` into a
request for `/page.html/`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp import web

from swe_mux.preview_store import PreviewStore
from swe_mux.preview_transport import preview_relative_path, preview_revision, preview_target
from swe_mux.processes import PreviewRegistry, is_static_file_server, preview_id

from .test_processes_phase4 import FakeSession, FakeSessionManager, session_record

PORT = 8766
HTTP_SERVER = "python.exe -m http.server 8766 --bind 127.0.0.1 --directory D:/site"


class ServerInspector:
    """One session listening on `PORT`, started by `command`."""

    def __init__(self, command: str = HTTP_SERVER) -> None:
        self.command = command

    def _group(self) -> dict[str, Any]:
        return {
            "available": True,
            "session_id": "session-a",
            "project_id": "default",
            "processes": [
                {
                    "pid": 44,
                    "command": self.command,
                    "listeners": [
                        {
                            "host": "127.0.0.1",
                            "port": PORT,
                            "loopback": True,
                            "url": f"http://127.0.0.1:{PORT}/",
                        }
                    ],
                }
            ],
        }

    async def snapshot(self, session_id: str, *, force: bool = False) -> dict[str, Any]:
        return self._group()

    async def snapshot_all(self) -> dict[str, Any]:
        return {"available": True, "sessions": [self._group()]}


def _sessions() -> Any:
    return FakeSessionManager({"session-a": FakeSession(session_record("session-a", "default"))})


def _registry(inspector: Any = None, store: PreviewStore | None = None) -> PreviewRegistry:
    async def not_browser_facing(_url: str) -> Any:
        from swe_mux.processes import PreviewProbeResult

        return PreviewProbeResult(False, 200, "text/html", "test")

    return PreviewRegistry(
        cast(Any, inspector or ServerInspector()),
        cast(Any, _sessions()),
        store=store,
        preview_probe=not_browser_facing,
    )


@pytest.mark.asyncio
async def test_a_printed_page_is_where_the_preview_opens_even_after_the_scan_found_it() -> None:
    registry = _registry()
    await registry.ensure_detected("default")
    scanned = next(iter(registry.items.values()))
    assert (scanned.url, scanned.entry) == (f"http://127.0.0.1:{PORT}/", "")

    opened = await registry.register(
        "session-a", f"http://127.0.0.1:{PORT}/cart-drawings.html", open_page=True
    )

    assert opened is scanned
    assert opened.entry == "cart-drawings.html"
    # The proxy base stays the origin, so the page's `/ref/x.jpg` still resolves.
    assert opened.url == f"http://127.0.0.1:{PORT}/"
    assert preview_target(opened, "ref/x.jpg")[0] == f"http://127.0.0.1:{PORT}/ref/x.jpg"


@pytest.mark.asyncio
async def test_a_page_clicked_before_the_scan_does_not_become_the_proxy_base() -> None:
    registry = _registry()
    opened = await registry.register(
        "session-a", f"http://127.0.0.1:{PORT}/docs/page.html", open_page=True
    )
    assert opened.url == f"http://127.0.0.1:{PORT}/"
    assert opened.entry == "docs/page.html"
    # The old shape asked the server for `/docs/page.html/`, a 404 on http.server.
    assert preview_target(opened, opened.entry)[0] == f"http://127.0.0.1:{PORT}/docs/page.html"


@pytest.mark.asyncio
async def test_selecting_the_server_does_not_undo_the_page_the_user_opened() -> None:
    registry = _registry()
    await registry.register("session-a", f"http://127.0.0.1:{PORT}/a.html", open_page=True)
    # Processes and the sidebar name the listener's root without naming a page.
    same = await registry.register("session-a", f"http://127.0.0.1:{PORT}/")
    assert same.entry == "a.html"
    # A root link the user followed is a page too.
    root = await registry.register("session-a", f"http://127.0.0.1:{PORT}/", open_page=True)
    assert root.entry == ""


@pytest.mark.asyncio
async def test_the_listener_scan_never_overwrites_a_chosen_page() -> None:
    registry = _registry()
    opened = await registry.register(
        "session-a", f"http://127.0.0.1:{PORT}/b.html", open_page=True
    )
    await registry.ensure_detected("default")
    assert opened.entry == "b.html"


@pytest.mark.asyncio
async def test_a_copied_preview_link_opens_at_the_page_it_names() -> None:
    registry = _registry()
    registry.reserved_ports = lambda: frozenset({8765})
    item = await registry.register("session-a", f"http://127.0.0.1:{PORT}/", open_page=True)
    again = await registry.register(
        "session-a", f"http://127.0.0.1:8765/preview/{item.id}/sub/page.html"
    )
    assert again is item
    assert item.entry == "sub/page.html"


@pytest.mark.asyncio
async def test_an_approved_preview_keeps_its_page_across_a_restart(tmp_path: Path) -> None:
    class Nobody(ServerInspector):
        def _group(self) -> dict[str, Any]:
            group = super()._group()
            group["processes"] = []
            return group

    registry = _registry(Nobody(), PreviewStore(tmp_path))
    approved = await registry.register(
        "session-a", f"http://127.0.0.1:{PORT}/one.html", approved=True, open_page=True
    )
    await registry.register(
        "session-a", f"http://127.0.0.1:{PORT}/two.html", approved=True, open_page=True
    )
    successor = _registry(Nobody(), PreviewStore(tmp_path))
    assert successor.items[approved.id].entry == "two.html"


def test_a_mirror_written_with_a_path_in_the_proxy_base_is_migrated(tmp_path: Path) -> None:
    """The live 07b8b55b registration: `/JAR%20Systems/campaigns/` as its base."""
    legacy_id = preview_id("default", "http", "127.0.0.1", 9763)
    (tmp_path / "previews.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "id": legacy_id,
                        "session_id": "session-a",
                        "project_id": "default",
                        "url": "http://127.0.0.1:9763/JAR%20Systems/campaigns/",
                        "host": "127.0.0.1",
                        "port": 9763,
                        "source": "user-approved",
                        "created_at": 1.0,
                        "entry": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = _registry(store=PreviewStore(tmp_path))
    item = registry.items[legacy_id]
    assert item.url == "http://127.0.0.1:9763/"
    assert item.entry == "JAR%20Systems/campaigns/"
    rewritten = json.loads((tmp_path / "previews.json").read_text(encoding="utf-8"))
    assert rewritten["items"][0]["url"] == "http://127.0.0.1:9763/"


@pytest.mark.parametrize(
    ("command", "static"),
    [
        (HTTP_SERVER, True),
        ("python3 -m SimpleHTTPServer 8000", True),
        (r"node C:\Users\me\AppData\npm\node_modules\http-server\bin\http-server -p 8080", True),
        (r"node C:\proj\node_modules\serve\build\main.js -l 3000", True),
        ("npx serve dist", True),
        ("php -S 127.0.0.1:8000", True),
        ("ruby -run -e httpd . -p 8000", True),
        ("miniserve .", True),
        (r"node C:\proj\node_modules\vite\bin\vite.js", False),
        ("python -m uvicorn app:app --reload", False),
        ("npx live-server", False),
        ("python manage.py runserver", False),
        # An argument that merely mentions the word is not the program.
        ("python app.py --serve", False),
        ("", False),
    ],
)
def test_a_plain_file_server_is_recognised_by_the_program_it_runs(
    command: str, static: bool
) -> None:
    assert is_static_file_server(command) is static


@pytest.mark.asyncio
async def test_the_scan_records_whether_the_listener_is_a_plain_file_server() -> None:
    plain = _registry(ServerInspector(HTTP_SERVER))
    await plain.ensure_detected("default")
    assert next(iter(plain.items.values())).static_server is True

    bundler = _registry(ServerInspector(r"node C:\proj\node_modules\vite\bin\vite.js"))
    await bundler.ensure_detected("default")
    assert next(iter(bundler.items.values())).static_server is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ("", "")),
        ("page.html", ("page.html", "")),
        ("app/?tab=2#top", ("app/", "tab=2")),
        ("/root-relative", ("root-relative", "")),
        ("http://169.254.169.254/latest", None),
        ("//evil.example/x", None),
        ("line\nbreak", None),
        (7, None),
    ],
)
def test_a_reported_page_path_is_refused_unless_it_is_a_plain_path(
    value: object, expected: tuple[str, str] | None
) -> None:
    assert preview_relative_path(value) == expected


class _Upstream:
    """A real loopback server on an OS-allocated port, so the fetch path is real."""

    def __init__(self) -> None:
        self.body = b"<html>one</html>"
        self.modified = "Thu, 25 Sep 2026 15:00:00 GMT"
        self.head_allowed = True
        self.runner: web.AppRunner | None = None
        self.port = 0

    async def page(self, request: web.Request) -> web.Response:
        if request.method == "HEAD" and not self.head_allowed:
            return web.Response(status=405)
        headers = {"Last-Modified": self.modified} if self.head_allowed else {}
        return web.Response(body=self.body, content_type="text/html", headers=headers)

    async def __aenter__(self) -> _Upstream:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.page)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        server = cast(Any, site)._server
        self.port = server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        assert self.runner is not None
        await self.runner.cleanup()


def _item(port: int) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(id="p", url=f"http://127.0.0.1:{port}/")


@pytest.mark.asyncio
async def test_the_revision_moves_when_the_server_serves_new_bytes() -> None:
    async with _Upstream() as upstream:
        item = _item(upstream.port)
        first = await preview_revision(item, ["page.html", "ref/a.jpg"])
        again = await preview_revision(item, ["page.html", "ref/a.jpg"])
        upstream.modified = "Thu, 25 Sep 2026 15:00:05 GMT"
        edited = await preview_revision(item, ["page.html", "ref/a.jpg"])
    assert first["revision"] == again["revision"]
    assert edited["revision"] != first["revision"]
    assert (first["checked"], first["unreachable"]) == (2, 0)


@pytest.mark.asyncio
async def test_a_server_without_head_validators_is_fingerprinted_by_its_body() -> None:
    async with _Upstream() as upstream:
        upstream.head_allowed = False
        item = _item(upstream.port)
        first = await preview_revision(item, ["page.html"])
        upstream.body = b"<html>two</html>"
        edited = await preview_revision(item, ["page.html"])
    assert edited["revision"] != first["revision"]


@pytest.mark.asyncio
async def test_an_unreachable_server_is_counted_rather_than_read_as_a_change() -> None:
    async with _Upstream() as upstream:
        port = upstream.port
    # The server is gone; the port was OS-allocated and released with it.
    result = await preview_revision(_item(port), ["page.html"])
    assert result["unreachable"] == 1


@pytest.mark.asyncio
async def test_a_revision_check_with_no_usable_path_is_refused() -> None:
    with pytest.raises(ValueError):
        await preview_revision(_item(1), ["https://example.com/", "//x"])
