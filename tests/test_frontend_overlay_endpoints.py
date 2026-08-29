"""The overlay over HTTP: the gates on the way in, and what a daemon really serves.

`test_frontend_overlay.py` covers the logic; this covers the two things only a
running daemon can answer.

The first is the gates. Installing replaces the application's UI, so it carries
the explicit-gesture header and is loopback-only, and both refusals have to be
the request's own answer rather than something a caller discovers by polling.

The second is the one that matters most and is easy to get wrong in a way no unit
test sees: **`create_app` must actually mount its static routes on the resolved
tree.** `FRONTEND_DIR` is read at four places inside `create_app` and by three
route modules; a resolution that set the key but ran after the `add_static` calls
would report an overlay in every status payload while serving the bundle from
`/assets`. So these tests ask the HTTP surface for a file, not the app key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import __version__
from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.frontend_overlay import OverlayStore, build_manifest, write_manifest
from swe_mux.server import create_app, wait_runtime_ready

GESTURE = "X-Mux-User-Gesture"

OVERLAY_INDEX = "<!doctype html><title>overlay</title>"
BUNDLED_INDEX = "<!doctype html><title>bundled</title>"


def _tree(root: Path, *, index: str, asset: str) -> Path:
    # Bytes rather than `write_text`, which translates "\n" to "\r\n" on Windows
    # and would make every body comparison here a test of the platform's line
    # endings instead of a test of which tree was served.
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_bytes(index.encode("utf-8"))
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "app.js").write_bytes(asset.encode("utf-8"))
    return root


def _installed_overlay(tmp_path: Path, data: Path, *, version: str = __version__) -> str:
    """Install a real overlay into `data` and return the asset body it carries."""
    source = _tree(tmp_path / "built", index=OVERLAY_INDEX, asset="// overlay asset\n")
    write_manifest(source, build_manifest(source, requires_backend=version))
    OverlayStore(data, backend_version=version).install_from_directory(source)
    return "// overlay asset\n"


def _config(tmp_path: Path, **overrides: object) -> Config:
    # The startup reconcile scans the real user home for every harness's past
    # transcripts; nothing here asserts anything about external history.
    return Config(data_dir=tmp_path / "data", reconcile_external_history=False, **overrides)


async def _client(app) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    await wait_runtime_ready(client.app)
    return client


@pytest.mark.asyncio
async def test_a_daemon_serves_the_overlay_over_http_not_just_in_its_app_key(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    asset = _installed_overlay(tmp_path, data)
    _tree(tmp_path / "bundled", index=BUNDLED_INDEX, asset="// bundled asset\n")

    client = await _client(create_app(_config(tmp_path)))
    try:
        # `/` and `/assets/...` come from two different readers of FRONTEND_DIR -
        # a handler and a static mount - so both are asked.
        assert await (await client.get("/")).text() == OVERLAY_INDEX
        assert await (await client.get("/assets/app.js")).text() == asset
        payload = await (await client.get("/api/frontend/overlay")).json()
        assert payload["serving"]["serving"] == "overlay"
        assert payload["active"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_daemon_with_no_overlay_serves_its_bundled_tree(tmp_path: Path) -> None:
    client = await _client(create_app(_config(tmp_path)))
    try:
        payload = await (await client.get("/api/frontend/overlay")).json()
        assert payload["supported"]
        assert not payload["installed"]
        assert payload["serving"]["serving"] == "bundled"
        assert payload["serving"]["reason"] == "no_overlay"
        assert not payload["serving"]["faulted"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_overlay_pinned_to_another_version_is_not_served_and_is_reported(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _installed_overlay(tmp_path, data, version="0.0.0-not-this-daemon")

    client = await _client(create_app(_config(tmp_path)))
    try:
        assert await (await client.get("/")).text() != OVERLAY_INDEX
        payload = await (await client.get("/api/frontend/overlay")).json()
        assert payload["installed"]
        assert payload["serving"]["serving"] == "bundled"
        assert payload["serving"]["reason"] == "version_mismatch"
        assert payload["serving"]["faulted"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_install_wide_switch_off_serves_the_bundle(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _installed_overlay(tmp_path, data)

    client = await _client(create_app(_config(tmp_path, frontend_overlay_enabled=False)))
    try:
        assert await (await client.get("/")).text() != OVERLAY_INDEX
        payload = await (await client.get("/api/frontend/overlay")).json()
        assert payload["serving"]["reason"] == "disabled"
        assert payload["installed"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_explicit_frontend_dir_override_beats_an_installed_overlay(
    tmp_path: Path,
) -> None:
    # Every test that points the daemon at a fixture tree has to stay independent
    # of whatever a data dir happens to contain, so an override wins outright and
    # says so rather than silently losing to an overlay.
    data = tmp_path / "data"
    data.mkdir()
    _installed_overlay(tmp_path, data)
    override = _tree(tmp_path / "override", index="<title>override</title>", asset="// o\n")

    client = await _client(create_app(_config(tmp_path), frontend_dir=override))
    try:
        assert await (await client.get("/")).text() == "<title>override</title>"
        payload = await (await client.get("/api/frontend/overlay")).json()
        assert payload["override"]
        assert payload["serving"] is None
        # The store still answers what is installed: that is a fact about the
        # data dir, not about what this process chose to serve.
        assert payload["installed"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_installing_requires_the_explicit_gesture_header(tmp_path: Path) -> None:
    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.post(
            "/api/frontend/overlay/install", json={"directory": str(tmp_path)}
        )
        assert response.status == 400
        assert "explicit user action" in (await response.json())["error"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_installing_needs_exactly_one_source(tmp_path: Path) -> None:
    client = await _client(create_app(_config(tmp_path)))
    try:
        for body in ({}, {"archive": "a.zip", "directory": "b"}):
            response = await client.post(
                "/api/frontend/overlay/install",
                json=body,
                headers={GESTURE: "frontend-overlay-install"},
            )
            assert response.status == 400
            assert (await response.json())["error"] == "source_required"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_refused_install_answers_the_request_rather_than_a_later_poll(
    tmp_path: Path,
) -> None:
    source = _tree(tmp_path / "unpinned", index=OVERLAY_INDEX, asset="// x\n")
    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.post(
            "/api/frontend/overlay/install",
            json={"directory": str(source)},
            headers={GESTURE: "frontend-overlay-install"},
        )
        assert response.status == 409
        body = await response.json()
        assert body["error"] == "manifest_missing"
        assert "compatibility pin" in body["message"]
        assert not (await (await client.get("/api/frontend/overlay")).json())["installed"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_url_install_without_a_digest_is_refused(tmp_path: Path) -> None:
    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.post(
            "/api/frontend/overlay/install",
            json={"url": "https://example.invalid/ui.zip"},
            headers={GESTURE: "frontend-overlay-install"},
        )
        assert response.status == 409
        assert (await response.json())["error"] == "digest_required"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_installing_over_http_then_reverting_takes_two_presses(tmp_path: Path) -> None:
    """The whole operator loop, from the API's side.

    Install answers 202 and says a restart applies it, because the static routes
    bind their directory at app construction - claiming otherwise would be the
    silent no-op this feature exists to end. Revert answers immediately and is
    equally explicit about it.
    """
    source = _tree(tmp_path / "built", index=OVERLAY_INDEX, asset="// overlay asset\n")
    write_manifest(source, build_manifest(source, requires_backend=__version__))

    client = await _client(create_app(_config(tmp_path)))
    try:
        installed = await client.post(
            "/api/frontend/overlay/install",
            json={"directory": str(source)},
            headers={GESTURE: "frontend-overlay-install"},
        )
        assert installed.status == 202
        body = await installed.json()
        assert body["installed"] and body["restart_required"]
        assert len(body["digest"]) == 64

        status = await (await client.get("/api/frontend/overlay")).json()
        assert status["installed"] and status["active"]
        # This process resolved before the install, so it is still on the bundle -
        # and says so, which is the honest answer rather than a hopeful one.
        assert status["serving"]["serving"] == "bundled"

        reverted = await client.post(
            "/api/frontend/overlay/revert",
            json={},
            headers={GESTURE: "frontend-overlay-revert"},
        )
        assert reverted.status == 200
        assert (await reverted.json())["changed"]
        after = await (await client.get("/api/frontend/overlay")).json()
        assert after["installed"] and not after["active"] and after["can_restore"]

        restored = await client.post(
            "/api/frontend/overlay/restore",
            json={},
            headers={GESTURE: "frontend-overlay-restore"},
        )
        assert (await restored.json())["changed"]
        assert (await (await client.get("/api/frontend/overlay")).json())["active"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reverting_and_restoring_each_require_their_own_gesture(tmp_path: Path) -> None:
    # Distinct gesture words rather than one shared "overlay" token: a client that
    # holds the header for one of these must not be able to spend it on the other.
    client = await _client(create_app(_config(tmp_path)))
    try:
        crossed = await client.post(
            "/api/frontend/overlay/revert",
            json={},
            headers={GESTURE: "frontend-overlay-restore"},
        )
        assert crossed.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reverting_with_nothing_installed_is_a_refusal_not_a_success(
    tmp_path: Path,
) -> None:
    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.post(
            "/api/frontend/overlay/revert",
            json={},
            headers={GESTURE: "frontend-overlay-revert"},
        )
        assert response.status == 409
        assert (await response.json())["error"] == "nothing_installed"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_status_endpoint_is_never_cached(tmp_path: Path) -> None:
    # A banner a cache kept alive across a revert would be the one bug this
    # feature can actually cause on its own.
    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.get("/api/frontend/overlay")
        assert response.headers["Cache-Control"] == "no-store"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_health_endpoint_reports_the_overlays_ui_build_id(tmp_path: Path) -> None:
    # `ui_build_id` is how a client tells which frontend a daemon is serving, and
    # it reads `FRONTEND_DIR`. Resolving an overlay has to move that answer too,
    # or every staleness check downstream is comparing against the wrong tree.
    data = tmp_path / "data"
    data.mkdir()
    build_id = "cd" * 32
    source = _tree(
        tmp_path / "built",
        index=f'<!doctype html><meta name="ui-build" content="{build_id}">',
        asset="// overlay asset\n",
    )
    write_manifest(source, build_manifest(source, requires_backend=__version__))
    OverlayStore(data, backend_version=__version__).install_from_directory(source)

    client = await _client(create_app(_config(tmp_path)))
    try:
        health = await (await client.get("/api/health")).json()
        assert health["ui_build_id"] == build_id
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_served_overlay_is_precompressed_like_any_other_tree(
    tmp_path: Path,
) -> None:
    """The interaction that is easy to miss, and would break the *second* start.

    The daemon runs `precompress_static` over whatever it serves, so it writes
    `.gz` sidecars into a verified overlay. Two things have to hold: a phone gets
    compressed bytes from an overlay exactly as it would from the bundle, and the
    tree still verifies afterwards - which is what makes the next start keep it.
    """
    data = tmp_path / "data"
    data.mkdir()
    source = _tree(
        tmp_path / "built",
        index=OVERLAY_INDEX,
        # Over the precompressor's 1 KiB floor, and compressible like a real chunk.
        asset="console.log('swe-mux');\n" * 400,
    )
    write_manifest(source, build_manifest(source, requires_backend=__version__))
    store = OverlayStore(data, backend_version=__version__)
    result = store.install_from_directory(source)

    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.get(
            "/assets/app.js", headers={"Accept-Encoding": "gzip"}, auto_decompress=False
        )
        assert response.status == 200
        assert response.headers["Content-Encoding"] == "gzip"
    finally:
        await client.close()

    from swe_mux.frontend_overlay import tree_path, verify_tree

    root = tree_path(data, result.digest)
    assert list(root.rglob("*.gz"))
    verdict = verify_tree(root, backend_version=__version__)
    assert verdict.ok, verdict.message


@pytest.mark.asyncio
async def test_a_corrupted_overlay_does_not_stop_a_daemon_from_starting(
    tmp_path: Path,
) -> None:
    """The safety property the design rests on, asserted end to end.

    A bad overlay must cost a stale frontend and a log line, never a daemon that
    will not start - because the daemon is what serves the endpoint that would
    fix it.
    """
    data = tmp_path / "data"
    data.mkdir()
    _installed_overlay(tmp_path, data)
    state = data / "frontend-overlay" / "state.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    tree = data / "frontend-overlay" / "trees" / payload["digest"]
    (tree / "assets" / "app.js").write_bytes(b"// corrupted\n")

    client = await _client(create_app(_config(tmp_path)))
    try:
        assert (await client.get("/api/health")).status == 200
        status = await (await client.get("/api/frontend/overlay")).json()
        assert status["serving"]["reason"] == "hash_mismatch"
        assert status["serving"]["faulted"]
        # Still revertible from a daemon that refused it, which is the point of
        # the endpoint surviving a broken overlay at all.
        reverted = await client.post(
            "/api/frontend/overlay/revert",
            json={},
            headers={GESTURE: "frontend-overlay-revert"},
        )
        assert (await reverted.json())["changed"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_an_overlay_built_against_other_endpoints_is_not_served(tmp_path: Path) -> None:
    """The half of the pin the version cannot supply, against a real route table.

    A frozen app is rebuilt from a checkout that moves between releases, so an
    overlay and a daemon can agree on `__version__` and disagree about which
    endpoints exist. The daemon's own `daemon_api_digest()` is what settles it,
    and this is the one place that check runs against the real table rather than
    a constant.
    """
    from swe_mux.frontend_overlay import build_manifest, write_manifest

    data = tmp_path / "data"
    data.mkdir()
    source = _tree(tmp_path / "built", index=OVERLAY_INDEX, asset="// overlay asset\n")
    write_manifest(
        source,
        build_manifest(source, requires_backend=__version__, requires_api="99" * 32),
    )
    # Installed past the store's own gate, which would refuse it for the same
    # reason: the question here is what a *daemon* does on finding one.
    OverlayStore(
        data, backend_version=__version__, api_digest="99" * 32
    ).install_from_directory(source)

    client = await _client(create_app(_config(tmp_path)))
    try:
        assert await (await client.get("/")).text() != OVERLAY_INDEX
        payload = await (await client.get("/api/frontend/overlay")).json()
        assert payload["serving"]["reason"] == "api_mismatch"
        assert payload["serving"]["faulted"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_installing_an_overlay_built_elsewhere_is_refused_by_the_daemon(
    tmp_path: Path,
) -> None:
    from swe_mux.frontend_overlay import build_manifest, write_manifest

    source = _tree(tmp_path / "built", index=OVERLAY_INDEX, asset="// overlay asset\n")
    write_manifest(
        source,
        build_manifest(source, requires_backend=__version__, requires_api="99" * 32),
    )
    client = await _client(create_app(_config(tmp_path)))
    try:
        response = await client.post(
            "/api/frontend/overlay/install",
            json={"directory": str(source)},
            headers={GESTURE: "frontend-overlay-install"},
        )
        assert response.status == 409
        body = await response.json()
        assert body["error"] == "api_mismatch"
        # The message has to say what to do, because "same version, different
        # endpoints" is the confusing case and a bare reason word would strand a
        # reader who can see both sides report 0.1.2.
        assert "redeploy" in body["message"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_choice_is_published_for_the_process_that_made_it(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _installed_overlay(tmp_path, data)
    app = create_app(_config(tmp_path))
    choice = app[keys.FRONTEND_CHOICE]
    assert choice.overlay_active
    assert app[keys.FRONTEND_DIR] == choice.directory
    assert choice.bundled != choice.directory
