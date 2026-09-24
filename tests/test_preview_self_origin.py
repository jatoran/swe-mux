"""A Preview can never point at swe-mux itself (the 2026-09-24 desktop freeze).

A user-approved registration for `http://127.0.0.1:8765/preview/...` put the daemon's
own origin into its Project's route table. Every preview document in that Project
then saw each of its same-origin URLs as a "Project service", and the runtime bridge
re-prefixed it on every mutation - an unbounded loop in the renderer that drew the
desktop app. These tests pin the registry half; the bridge half runs in a real
browser (`frontend/test/renderer/preview-bridge.spec.ts`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.preview_store import PreviewStore
from swe_mux.preview_transport import (
    PREVIEW_BRIDGE_PATH,
    _preview_runtime_bridge,
    rewrite_preview_html,
)
from swe_mux.processes import (
    PreviewDestinationReserved,
    PreviewRegistration,
    PreviewRegistry,
    listener_record,
)
from swe_mux.server import error_middleware
from tests.test_processes_phase4 import FakeInspector, fake_sessions

MUX_PORT = 8765
SUPERVISOR_PORT = 49462


def registry(**kwargs: Any) -> PreviewRegistry:
    return PreviewRegistry(
        cast(Any, FakeInspector()),
        cast(Any, fake_sessions()),
        reserved_ports=lambda: {MUX_PORT, SUPERVISOR_PORT},
        **kwargs,
    )


async def test_swe_mux_own_port_is_refused_even_with_approval() -> None:
    previews = registry()

    with pytest.raises(PreviewDestinationReserved) as refused:
        await previews.register("session-a", f"http://127.0.0.1:{MUX_PORT}/", approved=True)
    assert refused.value.code == "preview_destination_reserved"
    with pytest.raises(PreviewDestinationReserved):
        await previews.register("session-a", f"http://127.0.0.1:{SUPERVISOR_PORT}/", approved=True)
    with pytest.raises(PreviewDestinationReserved):
        # The exact shape registered on 2026-09-05: a preview route of an unknown id.
        await previews.register(
            "session-a",
            f"http://127.0.0.1:{MUX_PORT}/preview/e5e73c8d045ceccd/src/app/workers/sim-worker.ts/",
            approved=True,
        )
    assert previews.items == {}


async def test_a_copied_preview_link_opens_the_preview_it_names() -> None:
    previews = registry()
    existing = await previews.register("session-a", "http://127.0.0.1:4321/")

    reopened = await previews.register(
        "session-a", f"http://127.0.0.1:{MUX_PORT}/preview/{existing.id}/docs/", approved=True
    )

    assert reopened is existing
    assert len(previews.items) == 1


def test_a_persisted_self_registration_is_dropped_and_the_mirror_rewritten(
    tmp_path: Path,
) -> None:
    store = PreviewStore(tmp_path)
    poisoned = PreviewRegistration(
        "29bd51df4944f482",
        "session-a",
        "project-a",
        f"http://127.0.0.1:{MUX_PORT}/preview/e5e73c8d045ceccd/src/app/workers/sim-worker.ts/",
        "127.0.0.1",
        MUX_PORT,
        "user-approved",
        1788627941.36,
    )
    kept = PreviewRegistration(
        "37917d2344941573",
        "session-a",
        "project-a",
        "http://127.0.0.1:8770/",
        "127.0.0.1",
        8770,
        "user-approved",
        1787944218.47,
    )
    store.save([poisoned.snapshot(), kept.snapshot()])

    previews = registry(store=store)

    assert set(previews.items) == {kept.id}
    mirrored = json.loads((tmp_path / "previews.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in mirrored["items"]] == [kept.id]


def test_route_tables_never_map_swe_mux_whatever_the_registry_holds() -> None:
    previews = registry()
    for identity, port in (("self", MUX_PORT), ("backend", 37655)):
        previews.items[identity] = PreviewRegistration(
            identity,
            "session-a",
            "project-a",
            f"http://127.0.0.1:{port}/",
            "127.0.0.1",
            port,
            "user-approved",
            0.0,
        )

    assert previews.routes_for_project("project-a") == {
        "http://127.0.0.1:37655": "/preview/backend/"
    }


async def test_detection_ignores_a_listener_on_a_reserved_port() -> None:
    class Inspector(FakeInspector):
        async def snapshot_all(self) -> dict[str, Any]:
            return {
                "available": True,
                "sessions": [
                    {
                        "session_id": "session-a",
                        "project_id": "default",
                        "processes": [
                            {
                                "pid": 44,
                                "listeners": [
                                    listener_record("127.0.0.1", SUPERVISOR_PORT),
                                    listener_record("127.0.0.1", 4321),
                                ],
                            }
                        ],
                    }
                ],
            }

    async def never_browser(_url: str) -> Any:
        from swe_mux.processes import PreviewProbeResult

        return PreviewProbeResult(False, None, "", "test")

    previews = PreviewRegistry(
        cast(Any, Inspector()),
        cast(Any, fake_sessions()),
        preview_probe=never_browser,
        reserved_ports=lambda: {MUX_PORT, SUPERVISOR_PORT},
    )
    await previews.ensure_detected("default")

    assert {item.port for item in previews.items.values()} == {4321}


async def test_the_refusal_is_a_409_with_a_code_the_browser_branches_on() -> None:
    async def handler(_request: web.Request) -> web.Response:
        raise PreviewDestinationReserved("127.0.0.1:8765 is swe-mux itself")

    app = web.Application(middlewares=[error_middleware])
    app.router.add_post("/api/previews", handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/previews", json={})
        assert response.status == 409
        assert (await response.json())["code"] == "preview_destination_reserved"


def test_the_bridge_ships_as_one_asset_with_each_placeholder_once() -> None:
    source = PREVIEW_BRIDGE_PATH.read_text(encoding="utf-8")

    assert source.count("__MUX_PREVIEW_PREFIX__") == 1
    assert source.count("__MUX_PROJECT_ROUTES__") == 1
    bridge = _preview_runtime_bridge("/preview/abc/", {"http://127.0.0.1:37655": "/preview/b/"})
    assert "__MUX_" not in bridge.replace("__MUX_PREVIEW_BASE__", "")
    assert 'const prefix="/preview/abc/";' in bridge
    assert 'const projectRoutes={"http://127.0.0.1:37655":"/preview/b/"};' in bridge


def test_substituted_values_cannot_close_the_script_element() -> None:
    bridge = _preview_runtime_bridge("/preview/</script><b>/", {"x</script>": "y&z"})

    assert bridge.count("</script>") == 1
    assert bridge.endswith("</script>")
    assert "\\u003c/script\\u003e" in bridge
    assert "\\u0026" in bridge


def test_the_bridge_is_injected_once_at_the_head() -> None:
    rewritten = rewrite_preview_html(
        b"<!doctype html><html><head><title>t</title></head><body><a href='x/'>x</a></body>",
        "/preview/abc/",
        {},
    ).decode("utf-8")

    assert rewritten.index("<script>(function(){") == rewritten.index("<head>") + len("<head>")
    assert rewritten.count("window.__MUX_PREVIEW_BASE__=prefix") == 1
    # The two properties the incident broke, as the shipped source states them.
    assert "const pageOrigin=canonicalOrigin(new URL(location.href));" in rewritten
    assert "if(alreadyRouted(url))return url.toString();" in rewritten
