"""Production UI identity and cache contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web

from swe_mux import app_keys as keys
from swe_mux import server
from swe_mux.ui_build import parse_ui_build_id, read_ui_build_id


def test_build_identity_parser_requires_a_valid_meta_value() -> None:
    build_id = "a" * 64
    html = f'<html><head><meta name="ui-build" content="{build_id}"></head>'
    assert parse_ui_build_id(html) == build_id
    assert parse_ui_build_id('<meta name="ui-build" content="not-a-hash">') is None
    assert parse_ui_build_id("<html><head></head></html>") is None


def test_build_identity_stat_cache_observes_a_source_rebuild(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    first = "a" * 64
    second = "b" * 64
    index.write_text(f'<meta name="ui-build" content="{first}">', encoding="utf-8")
    assert read_ui_build_id(tmp_path) == first

    previous = index.stat()
    index.write_text(f'<meta name="ui-build" content="{second}">', encoding="utf-8")
    os.utime(index, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
    assert read_ui_build_id(tmp_path) == second


def test_build_identity_reader_treats_invalid_or_missing_html_as_unidentified(
    tmp_path: Path,
) -> None:
    index = tmp_path / "index.html"
    index.write_bytes(b"\xff\xfe")
    assert read_ui_build_id(tmp_path) is None
    assert read_ui_build_id(tmp_path / "missing") is None


async def test_health_exposes_the_served_ui_identity(tmp_path: Path) -> None:
    build_id = "c" * 64
    (tmp_path / "index.html").write_text(
        f'<meta name="ui-build" content="{build_id}">', encoding="utf-8"
    )
    request = SimpleNamespace(app={keys.FRONTEND_DIR: tmp_path})
    response = await server.health(request)  # type: ignore[arg-type]
    assert json.loads(response.body)["ui_build_id"] == build_id


def test_static_cache_policy_revalidates_html_and_immutably_caches_assets() -> None:
    document = web.Response()
    server._apply_static_cache_headers(  # type: ignore[arg-type]
        document, SimpleNamespace(path="/")
    )
    assert document.headers["Cache-Control"] == "no-cache, must-revalidate"

    asset = web.Response()
    server._apply_static_cache_headers(  # type: ignore[arg-type]
        asset, SimpleNamespace(path="/assets/index-content.js")
    )
    assert asset.headers["Cache-Control"] == "public, max-age=31536000, immutable"

    api_response = web.Response()
    server._apply_static_cache_headers(  # type: ignore[arg-type]
        api_response, SimpleNamespace(path="/api/health")
    )
    assert "Cache-Control" not in api_response.headers
