"""Static document previews: a directory of the checkout served as a Preview.

The point of the feature is that it reuses the Preview registry rather than being
a second viewer, so most of what needs proving here is that the *shared* surfaces
still behave: a stable `/preview/<id>/` route a phone can bookmark, a registration
that survives a restart, and a proxy that refuses everything outside the directory
it was told to serve.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.preview_store import PreviewStore
from swe_mux.preview_transport import (
    PREVIEW_HTTP_CONCURRENCY,
    PREVIEW_WS_CONCURRENCY,
    preview_proxy,
    static_preview_content_type,
)
from swe_mux.processes import PreviewRegistry, static_preview_id
from swe_mux.project_files import is_static_preview_entry, read_static_preview_file
from swe_mux.routes.processes import _register_static_preview
from swe_mux.server import security_middleware

pytestmark = pytest.mark.filterwarnings(
    "ignore:It is recommended to use web.AppKey instances for keys"
)


class FakeInspector:
    def live_listeners(self) -> set[tuple[str, int, str]]:
        return set()


def _registry(store: PreviewStore | None = None) -> PreviewRegistry:
    sessions = SimpleNamespace(sessions={})
    return PreviewRegistry(cast(Any, FakeInspector()), cast(Any, sessions), store=store)


def _page(root: Path, name: str = "index.html", body: str = "<html><head></head></html>") -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_the_same_document_comes_back_under_the_same_route(tmp_path: Path) -> None:
    """The id is the URL a phone bookmarked, so a restart must not re-key it."""
    first = _registry().register_static(
        project_id="default", doc_root=str(tmp_path), entry="index.html"
    )
    second = _registry().register_static(
        project_id="default", doc_root=str(tmp_path), entry="index.html"
    )
    assert first.id == second.id != ""


def test_previewing_the_same_file_twice_reactivates_one_registration(tmp_path: Path) -> None:
    """"Preview" is safe to press twice: it must not mint a rival on a new URL."""
    registry = _registry()
    first = registry.register_static(
        project_id="default", doc_root=str(tmp_path), entry="index.html", label="index.html"
    )
    second = registry.register_static(
        project_id="default", doc_root=str(tmp_path), entry="index.html", label="index.html"
    )
    assert first is second
    assert len(registry.items) == 1


def test_distinct_documents_never_collide(tmp_path: Path) -> None:
    identities = {
        static_preview_id("default", "", str(tmp_path), "index.html"),
        static_preview_id("default", "", str(tmp_path), "other.html"),
        static_preview_id("default", "", str(tmp_path / "sub"), "index.html"),
        static_preview_id("default", str(tmp_path / "wt"), str(tmp_path), "index.html"),
        static_preview_id("other", "", str(tmp_path), "index.html"),
    }
    assert len(identities) == 5


def test_a_delimiter_cannot_forge_another_documents_id() -> None:
    """The separator is chr(0), which no path or project id can contain."""
    assert static_preview_id("a", "", "b", "c") != static_preview_id("a\x00", "", "b", "c")


def test_a_static_preview_is_never_confused_with_a_loopback_endpoint(tmp_path: Path) -> None:
    """Its `file://` url must not enter the bridge's origin map as a dialable service."""
    registry = _registry()
    registry.register_static(project_id="default", doc_root=str(tmp_path), entry="index.html")
    assert registry.routes_for_project("default") == {}


# ---------------------------------------------------------------------------
# Lifetime
# ---------------------------------------------------------------------------


def test_a_static_preview_outlives_the_daemon(tmp_path: Path) -> None:
    """Nothing rediscovers it: there is no listener to poll, only bytes on disk."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    served = tmp_path / "site"
    served.mkdir()
    original = _registry(PreviewStore(data_dir)).register_static(
        project_id="default",
        doc_root=str(served),
        entry="index.html",
        doc_root_relative="site",
        label="index.html",
    )

    successor = _registry(PreviewStore(data_dir))
    restored = successor.items[original.id]
    assert restored.kind == "static"
    assert restored.doc_root == str(served)
    assert restored.entry == "index.html"
    assert restored.doc_root_relative == "site"
    assert restored.label == "index.html"


def test_pruning_never_reaps_a_static_preview(tmp_path: Path) -> None:
    """It has no listener, so an absent listener is not evidence of anything."""
    registry = _registry()
    item = registry.register_static(
        project_id="default", doc_root=str(tmp_path), entry="index.html"
    )
    assert registry.prune(now=10_000_000.0) == []
    assert item.id in registry.items


def test_a_stored_static_row_without_a_directory_is_dropped(tmp_path: Path) -> None:
    """It would occupy a sidebar row that can serve nothing."""
    (tmp_path / "previews.json").write_text(
        json.dumps(
            {
                "items": [
                    {"id": "a", "url": "file:///x/index.html", "kind": "static", "entry": "i.html"},
                    {"id": "b", "url": "file:///x/index.html", "kind": "static", "doc_root": "/x"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert PreviewStore(tmp_path).load() == []


def test_a_static_row_restores_without_a_host_or_port(tmp_path: Path) -> None:
    """The loopback routing guard must not reject a kind that never had those."""
    (tmp_path / "previews.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "a",
                        "session_id": "",
                        "project_id": "default",
                        "url": "file:///x/index.html",
                        "host": "",
                        "source": "static",
                        "created_at": 1.0,
                        "kind": "static",
                        "doc_root": "/x",
                        "entry": "index.html",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    restored = PreviewStore(tmp_path).load()
    assert len(restored) == 1
    assert restored[0]["port"] == 0


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_an_empty_tail_serves_the_entry_document(tmp_path: Path) -> None:
    _page(tmp_path, "page.html", "<h1>hi</h1>")
    data, resolved, _size = read_static_preview_file(tmp_path, "", "page.html", 1024)
    assert data == b"<h1>hi</h1>"
    assert resolved == "page.html"


def test_a_directory_resolves_to_its_index(tmp_path: Path) -> None:
    _page(tmp_path, "docs/index.html", "<p>docs</p>")
    data, resolved, _size = read_static_preview_file(tmp_path, "docs/", "page.html", 1024)
    assert data == b"<p>docs</p>"
    assert resolved == "docs/index.html"


@pytest.mark.parametrize("tail", ["../secret.txt", "docs/../../secret.txt"])
def test_a_path_outside_the_served_directory_is_refused(tmp_path: Path, tail: str) -> None:
    served = tmp_path / "site"
    served.mkdir()
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    with pytest.raises(ValueError):
        read_static_preview_file(served, tail, "index.html", 1024)


def test_an_absolute_tail_never_reaches_the_file_it_names(tmp_path: Path) -> None:
    """`str(secret)` is a different input on each host, and both must miss.

    On Windows a drive-lettered tail is still absolute after the route-separator
    strip, so `project_path` refuses it outright. On POSIX the leading `/` *is*
    the route separator - the rule the test below states - so the same string
    arrives as an ordinary root-relative tail, resolves inside the served
    directory, and misses. Two exceptions, one guarantee; the guarantee is what
    is asserted here, because asserting the exception is asserting the host.
    """
    served = tmp_path / "site"
    served.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        read_static_preview_file(served, str(secret), "index.html", 1024)


@pytest.mark.skipif(
    os.name == "nt",
    reason="a drive-lettered tail stays absolute past the strip, so it never re-roots",
)
def test_an_absolute_posix_tail_is_re_rooted_rather_than_followed(tmp_path: Path) -> None:
    """The POSIX half stated positively, because a miss on its own proves little.

    A tail that *names* an outside file is served from inside the served
    directory or not at all. Planting that same relative path under the root and
    getting that copy back is the evidence that the absolute path was re-rooted
    rather than followed - which is why the miss above is a miss and not a leak.
    """
    served = tmp_path / "site"
    served.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("outside", encoding="utf-8")
    inside = served / str(outside).lstrip("/")
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("inside", encoding="utf-8")

    data, resolved, _size = read_static_preview_file(served, str(outside), "index.html", 1024)
    assert data == b"inside"
    assert resolved == str(outside).lstrip("/")
    assert (served / resolved).resolve() == inside.resolve()


def test_a_symlink_inside_the_directory_cannot_reach_outside_it(tmp_path: Path) -> None:
    """A crafted tail is refused before resolution; a symlink only after it.

    The `..` and absolute checks run against the string, so nothing there sees
    this: the tail is an ordinary relative path and only the post-resolve
    re-check against the root catches where it landed.
    """
    served = tmp_path / "site"
    served.mkdir()
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    try:
        (served / "escape").symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:  # Windows needs a privilege this host may not hold.
        pytest.skip(f"symlinks unavailable here: {exc}")
    with pytest.raises(ValueError):
        read_static_preview_file(served, "escape/secret.txt", "index.html", 1024)


def test_a_leading_slash_is_the_route_separator_and_not_an_escape(tmp_path: Path) -> None:
    """`/app.css` in a rewritten page arrives as a root-relative tail, not an escape.

    It resolves inside the served directory, so it is an ordinary hit or miss -
    the guard that matters is that it can never reach the parent.
    """
    served = tmp_path / "site"
    served.mkdir()
    (served / "app.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    data, resolved, _size = read_static_preview_file(served, "/app.css", "index.html", 1024)
    assert data == b"body{}"
    assert resolved == "app.css"
    with pytest.raises(FileNotFoundError):
        read_static_preview_file(served, "/secret.txt", "index.html", 1024)


def test_a_missing_file_is_a_miss_and_not_a_refusal(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_static_preview_file(tmp_path, "nope.css", "index.html", 1024)


def test_an_oversized_file_is_reported_rather_than_read(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"x" * 64)
    data, _resolved, size = read_static_preview_file(tmp_path, "big.bin", "index.html", 16)
    assert data is None
    assert size == 64


def test_the_entry_allowlist_is_pages_only() -> None:
    assert is_static_preview_entry("a/b/index.html")
    assert is_static_preview_entry("PAGE.HTM")
    assert is_static_preview_entry("doc.xhtml")
    assert not is_static_preview_entry("style.css")
    assert not is_static_preview_entry("notes.md")
    assert not is_static_preview_entry("README")


def test_web_content_types_are_stated_and_not_asked_for() -> None:
    """On Windows `mimetypes` reads the registry, where `.js` is often text/plain.

    With `nosniff` on every response that renders the page scriptless and unstyled
    with nothing in the network log to explain it.
    """
    assert static_preview_content_type("app.js").startswith("text/javascript")
    assert static_preview_content_type("app.mjs").startswith("text/javascript")
    assert static_preview_content_type("main.css").startswith("text/css")
    assert static_preview_content_type("index.html").startswith("text/html")
    assert static_preview_content_type("logo.svg") == "image/svg+xml"
    assert static_preview_content_type("blob.unknownext") == "application/octet-stream"


# ---------------------------------------------------------------------------
# Registration through the endpoint's own validation
# ---------------------------------------------------------------------------


def _register_request(root: Path, registry: PreviewRegistry) -> Any:
    project = SimpleNamespace(id="default", root=str(root))
    return SimpleNamespace(
        app={
            keys.PROJECTS: SimpleNamespace(projects={"default": project}),
            keys.PREVIEWS: registry,
        }
    )


def test_registration_serves_the_pages_own_folder(tmp_path: Path) -> None:
    """A page's `./style.css` is the normal case; serving one file would 404 it."""
    _page(tmp_path, "site/page.html")
    registry = _registry()
    item = asyncio.run(
        _register_static_preview(
            _register_request(tmp_path, registry),
            {"project_id": "default", "path": "site/page.html"},
        )
    )
    assert item.doc_root == str((tmp_path / "site").resolve())
    assert item.entry == "page.html"
    assert item.doc_root_relative == "site"
    assert item.label == "page.html"
    assert item.session_id == ""


def test_project_scope_widens_the_served_directory_to_the_checkout(tmp_path: Path) -> None:
    """For a built page whose absolute paths are repo-root-relative."""
    _page(tmp_path, "dist/index.html")
    item = asyncio.run(
        _register_static_preview(
            _register_request(tmp_path, _registry()),
            {"project_id": "default", "path": "dist/index.html", "scope": "project"},
        )
    )
    assert item.doc_root == str(tmp_path.resolve())
    assert item.entry == "dist/index.html"
    assert item.doc_root_relative == ""


@pytest.mark.parametrize("path", ["notes.md", "style.css", "../outside.html", "missing.html"])
def test_registration_refuses_anything_that_is_not_a_page_in_this_checkout(
    tmp_path: Path, path: str
) -> None:
    _page(tmp_path, "notes.md")
    _page(tmp_path, "style.css")
    with pytest.raises(ValueError):
        asyncio.run(
            _register_static_preview(
                _register_request(tmp_path, _registry()),
                {"project_id": "default", "path": path},
            )
        )


def test_registration_refuses_an_unknown_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown project"):
        asyncio.run(
            _register_static_preview(
                _register_request(tmp_path, _registry()),
                {"project_id": "nope", "path": "index.html"},
            )
        )


# ---------------------------------------------------------------------------
# The proxy route
# ---------------------------------------------------------------------------


def _proxy_application(registry: PreviewRegistry) -> web.Application:
    app = web.Application(middlewares=[security_middleware], client_max_size=12 * 1024 * 1024)
    app[keys.PREVIEWS] = registry
    # Deliberately empty: a static preview has no owning session, and the route
    # must not be gated on one existing.
    app[keys.SESSIONS] = SimpleNamespace(sessions={})
    app[keys.PREVIEW_HTTP_SEMAPHORE] = asyncio.Semaphore(PREVIEW_HTTP_CONCURRENCY)
    app[keys.PREVIEW_WS_SEMAPHORE] = asyncio.Semaphore(PREVIEW_WS_CONCURRENCY)
    app.router.add_route("*", "/preview/{preview_id}/{tail:.*}", preview_proxy)
    return app


def _served(tmp_path: Path) -> tuple[PreviewRegistry, str]:
    _page(tmp_path, "index.html", '<html><head></head><body><script src="/app.js"></script></body>')
    (tmp_path / "app.js").write_text('import "/lib.js"\n', encoding="utf-8")
    (tmp_path / "style.css").write_text("body{background:url(/bg.png)}", encoding="utf-8")
    registry = _registry()
    item = registry.register_static(
        project_id="default", doc_root=str(tmp_path), entry="index.html", label="index.html"
    )
    return registry, item.id


@pytest.mark.asyncio
async def test_the_entry_is_served_sandboxed_and_rewritten(tmp_path: Path) -> None:
    registry, identity = _served(tmp_path)
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        response = await client.get(f"/preview/{identity}/")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        # The document runs on the mux origin, and this origin *is* the authority.
        # An opaque origin is what stops an externally opened page reaching /api.
        assert "sandbox allow-scripts" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-cache"
        body = await response.text()
        assert f'src="/preview/{identity}/app.js"' in body
        assert "__MUX_PREVIEW_BASE__" in body


@pytest.mark.asyncio
async def test_subresources_keep_their_real_types_and_are_rewritten(tmp_path: Path) -> None:
    registry, identity = _served(tmp_path)
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        script = await client.get(f"/preview/{identity}/app.js")
        assert script.headers["Content-Type"].startswith("text/javascript")
        assert f'"/preview/{identity}/lib.js"' in await script.text()

        sheet = await client.get(f"/preview/{identity}/style.css")
        assert sheet.headers["Content-Type"].startswith("text/css")
        assert f"url(/preview/{identity}/bg.png)" in await sheet.text()


@pytest.mark.asyncio
async def test_the_route_is_not_gated_on_a_live_session(tmp_path: Path) -> None:
    """A document in a Project outlives every session that could have opened it."""
    registry, identity = _served(tmp_path)
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        assert (await client.get(f"/preview/{identity}/")).status == 200


@pytest.mark.asyncio
async def test_the_proxy_refuses_to_leave_the_served_directory(tmp_path: Path) -> None:
    served = tmp_path / "site"
    served.mkdir()
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    _page(served, "index.html")
    registry = _registry()
    identity = registry.register_static(
        project_id="default", doc_root=str(served), entry="index.html"
    ).id
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        assert (await client.get(f"/preview/{identity}/../secret.txt")).status in (403, 404)
        # Percent-encoded traversal must not be decoded into a second path segment.
        assert (await client.get(f"/preview/{identity}/%2e%2e/secret.txt")).status in (403, 404)
        assert (await client.get(f"/preview/{identity}/nope.css")).status == 404


@pytest.mark.asyncio
async def test_a_percent_encoded_name_is_decoded_exactly_once(tmp_path: Path) -> None:
    """The router hands over a decoded tail; decoding it again would turn `%252e%252e`
    into a traversal that the containment check never got to see as one."""
    registry, identity = _served(tmp_path)
    (tmp_path / "my page.css").write_text("body{}", encoding="utf-8")
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        hit = await client.get(f"/preview/{identity}/my%20page.css")
        assert hit.status == 200
        assert await hit.text() == "body{}"
        assert (await client.get(f"/preview/{identity}/%252e%252e/secret.txt")).status == 404


@pytest.mark.asyncio
async def test_a_static_preview_is_read_only(tmp_path: Path) -> None:
    """There is no upstream here, so a write has nothing it could mean."""
    registry, identity = _served(tmp_path)
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        origin = str(client.make_url("/")).rstrip("/")
        response = await client.post(
            f"/preview/{identity}/index.html", data=b"x", headers={"Origin": origin}
        )
        assert response.status == 405


@pytest.mark.asyncio
async def test_head_answers_without_a_body(tmp_path: Path) -> None:
    registry, identity = _served(tmp_path)
    async with TestClient(TestServer(_proxy_application(registry))) as client:
        response = await client.head(f"/preview/{identity}/")
        assert response.status == 200
        assert await response.read() == b""
