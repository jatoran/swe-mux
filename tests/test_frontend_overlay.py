"""The frontend overlay: verification, the compatibility pin, and the revert.

Three properties carry this feature, and each of them fails silently if it stops
working - which is the whole reason the tests are here rather than in a live
check. A verification that stopped hashing would serve whatever is on disk; a pin
that stopped comparing would serve a frontend against a daemon it was never built
for; and a revert that did not take would leave the operator looking at the thing
they just turned off. All three are pure logic over a temporary directory, so all
three are answerable here.

What is **not** answerable here is the end-to-end question: does the *frozen*
desktop app, serving from `dist/swe-mux/_internal/swe_mux/static`, actually prefer
an overlay in `~/.mux`. That needs a real frozen app and a real data dir, and it
is recorded as owed rather than faked. Everything up to the resolution call is
covered; the resolution call itself is covered against a synthetic bundled tree.
"""

from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

import pytest

from swe_mux.build_support import precompress_static
from swe_mux.frontend_overlay import (
    ARCHIVE_ROOT,
    MANIFEST_NAME,
    OverlayRefused,
    OverlayStore,
    build_manifest,
    daemon_api_digest,
    extract_overlay,
    file_digest,
    pack_overlay,
    parse_manifest,
    read_manifest,
    resolve_frontend_dir,
    route_table_digest,
    state_path,
    tree_digest,
    tree_path,
    verify_tree,
    write_manifest,
)

BACKEND = "9.9.9"
#: A stand-in route-table digest. Every helper below passes it explicitly, so the
#: whole file is independent of the real route table - a test that reddened when
#: an unrelated endpoint was added would be testing the wrong thing.
API = "1a" * 32

INDEX = (
    "<!doctype html><html><head>"
    '<meta name="ui-build" content="' + "ab" * 32 + '">'
    '<script type="module" src="/assets/index-deadbeef.js"></script>'
    "</head><body><div id=root></div></body></html>"
)


def make_tree(root: Path, *, index: str = INDEX, extra: dict[str, str] | None = None) -> Path:
    """A minimal but realistic built frontend: an index and a hashed asset."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(index, encoding="utf-8")
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    # Over the precompressor's 1 KiB floor, so the sidecar paths are exercised.
    (assets / "index-deadbeef.js").write_text("console.log('hi')\n" + "// pad\n" * 200)
    (assets / "index-deadbeef.css").write_text("body{color:red}\n" + "/* pad */\n" * 200)
    for name, body in (extra or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def install_tree(root: Path, *, requires_backend: str = BACKEND, requires_api: str = API) -> Path:
    """A tree carrying its own manifest, the shape a payload actually arrives in."""
    write_manifest(
        root,
        build_manifest(root, requires_backend=requires_backend, requires_api=requires_api),
    )
    return root


# --- the manifest ---------------------------------------------------------------


def test_a_manifest_describes_every_payload_file_and_no_derived_one(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "static")
    precompress_static(tree)
    manifest = build_manifest(tree, requires_backend=BACKEND, requires_api=API)
    assert set(manifest.files) == {
        "index.html",
        "assets/index-deadbeef.js",
        "assets/index-deadbeef.css",
    }
    # The sidecars exist on disk and are deliberately absent from the manifest:
    # they are derived from listed files, and the daemon regenerates them.
    assert (tree / "assets" / "index-deadbeef.js.gz").is_file()
    assert not any(name.endswith(".gz") for name in manifest.files)
    assert manifest.requires_backend == BACKEND
    assert manifest.ui_build_id == "ab" * 32


def test_a_tree_with_no_index_is_not_a_frontend(tmp_path: Path) -> None:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "orphan.js").write_text("x")
    with pytest.raises(OverlayRefused) as refusal:
        build_manifest(root, requires_backend=BACKEND, requires_api=API)
    assert refusal.value.reason == "no_index"


def test_the_tree_digest_does_not_depend_on_iteration_order() -> None:
    files = {"b.js": "b" * 64, "a.js": "a" * 64, "c/d.js": "c" * 64}
    assert tree_digest(files) == tree_digest(dict(reversed(list(files.items()))))


def _payload(files: dict[str, str], **extra: object) -> dict[str, object]:
    return {
        "schema": 1,
        "requires_backend": BACKEND,
        "requires_api": API,
        "files": files,
        "tree_digest": tree_digest(files),
        **extra,
    }


def test_a_manifest_that_disagrees_with_itself_is_refused() -> None:
    with pytest.raises(OverlayRefused) as refusal:
        parse_manifest(_payload({"index.html": "a" * 64}, tree_digest="f" * 64))
    assert refusal.value.reason == "manifest_inconsistent"


def test_a_manifest_naming_a_path_outside_the_tree_is_refused() -> None:
    for escape in ("../evil.js", "/etc/passwd", "C:/windows/system32/evil.js"):
        with pytest.raises(OverlayRefused) as refusal:
            parse_manifest(_payload({escape: "a" * 64}))
        assert refusal.value.reason == "manifest_unreadable", escape


def test_a_manifest_from_a_newer_schema_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(OverlayRefused) as refusal:
        parse_manifest({"schema": 99, "requires_backend": BACKEND, "files": {}})
    assert refusal.value.reason == "unsupported_schema"


def test_a_manifest_without_a_version_pin_is_not_a_manifest() -> None:
    payload = _payload({"index.html": "a" * 64})
    del payload["requires_backend"]
    with pytest.raises(OverlayRefused) as refusal:
        parse_manifest(payload)
    assert refusal.value.reason == "manifest_unreadable"


def test_a_manifest_without_an_api_pin_is_not_a_manifest() -> None:
    # Required rather than optional, and this is the assertion that keeps it so:
    # an optional pin is one any producer can decline to make, which is the same
    # as not having one.
    payload = _payload({"index.html": "a" * 64})
    del payload["requires_api"]
    with pytest.raises(OverlayRefused) as refusal:
        parse_manifest(payload)
    assert refusal.value.reason == "manifest_unreadable"


def test_a_manifest_round_trips_through_its_own_json(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    written = read_manifest(tree)
    rebuilt = build_manifest(tree, requires_backend=BACKEND, requires_api=API)
    assert written.tree_digest == rebuilt.tree_digest
    assert written.files == rebuilt.files
    assert written.requires_api == API


# --- verification -----------------------------------------------------------------


def test_a_verified_tree_passes(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert verdict.ok, verdict.message
    assert verdict.reason == "ok"


def test_a_changed_byte_fails_the_hash(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    asset = tree / "assets" / "index-deadbeef.js"
    asset.write_text(asset.read_text() + "// tampered\n")
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "hash_mismatch"
    assert "index-deadbeef.js" in verdict.message


def test_a_missing_listed_file_fails(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    (tree / "assets" / "index-deadbeef.css").unlink()
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "missing_file"


def test_an_unlisted_file_fails_because_a_mostly_specified_tree_is_not_verified(
    tmp_path: Path,
) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    (tree / "assets" / "smuggled.js").write_text("evil()")
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "unexpected_file"
    assert "smuggled.js" in verdict.message


def test_a_sidecar_the_daemon_wrote_after_verification_is_accepted(tmp_path: Path) -> None:
    # The daemon runs `precompress_static` over whatever tree it serves, so a
    # `.gz` appears in a verified overlay between one start and the next. If that
    # were an unexpected file, every overlay would fail its *second* start - the
    # kind of defect a single-start test cannot see.
    tree = install_tree(make_tree(tmp_path / "static"))
    assert verify_tree(tree, backend_version=BACKEND, api_digest=API).ok
    precompress_static(tree)
    assert (tree / "assets" / "index-deadbeef.js.gz").is_file()
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert verdict.ok, verdict.message


def test_a_planted_sidecar_that_does_not_match_its_sibling_is_refused(tmp_path: Path) -> None:
    # The attack a bare "ignore .gz" rule would allow: aiohttp serves a `.gz`
    # beside a file to every browser, so a sidecar whose contents differ from its
    # sibling replaces the page while the hashed sibling still verifies.
    tree = install_tree(make_tree(tmp_path / "static"))
    (tree / "index.html.gz").write_bytes(gzip.compress(b"<html>not the app</html>"))
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "unexpected_file"
    assert "sidecar" in verdict.message


def test_a_sidecar_for_a_file_not_in_the_manifest_is_refused(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    (tree / "assets" / "ghost.js.gz").write_bytes(gzip.compress(b"x"))
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "unexpected_file"


def test_a_tree_with_no_manifest_declares_no_pin_and_is_refused(tmp_path: Path) -> None:
    tree = make_tree(tmp_path / "static")
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "manifest_missing"


def test_an_unreadable_manifest_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"))
    (tree / MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "manifest_unreadable"


# --- the compatibility pin ---------------------------------------------------------


def test_an_overlay_pinned_to_another_backend_is_refused(tmp_path: Path) -> None:
    tree = install_tree(make_tree(tmp_path / "static"), requires_backend="0.0.1")
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "version_mismatch"
    assert "0.0.1" in verdict.message and BACKEND in verdict.message


def test_the_pin_is_exact_equality_so_an_app_update_supersedes_an_overlay(
    tmp_path: Path,
) -> None:
    # Both directions, because the rule an operator has to hold in their head is
    # "an app update always supersedes an overlay" and a one-sided comparison
    # would quietly keep serving a frontend across a release in one direction.
    tree = install_tree(make_tree(tmp_path / "static"), requires_backend="1.2.3")
    assert verify_tree(tree, backend_version="1.2.3", api_digest=API).ok
    assert verify_tree(tree, backend_version="1.2.4", api_digest=API).reason == "version_mismatch"
    assert verify_tree(tree, backend_version="1.2.2", api_digest=API).reason == "version_mismatch"


def test_an_overlay_built_against_other_endpoints_is_refused(tmp_path: Path) -> None:
    # The case the version pin structurally cannot catch, and the reason the API
    # digest exists: both sides say the same version, because the frozen app is
    # rebuilt from a checkout that moves between releases while `__version__`
    # does not. A frontend calling a route this daemon does not serve would fail
    # arbitrarily rather than legibly.
    tree = install_tree(make_tree(tmp_path / "static"), requires_api="99" * 32)
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    assert not verdict.ok
    assert verdict.reason == "api_mismatch"
    assert BACKEND in verdict.message


def test_the_api_digest_ignores_registration_order_and_duplicates() -> None:
    # Registration order is load-bearing for aiohttp's resolution and is not a
    # compatibility fact; a digest that moved with it would refuse every overlay
    # after any reordering, for nothing.
    table = [("GET", "/a"), ("POST", "/b"), ("GET", "/c")]
    assert route_table_digest(table) == route_table_digest(list(reversed(table)))
    assert route_table_digest(table) == route_table_digest([*table, ("GET", "/a")])
    assert route_table_digest(table) != route_table_digest([*table, ("GET", "/d")])
    # A method change is a compatibility fact and must move it.
    assert route_table_digest([("GET", "/a")]) != route_table_digest([("POST", "/a")])


def test_this_daemons_api_digest_describes_its_real_route_table() -> None:
    # The two sides of the pin have to be the same computation, so the producer's
    # helper and the daemon's own answer are checked against each other rather
    # than each against a constant.
    from swe_mux import routes

    expected = route_table_digest((route.method, route.path) for route in routes.all_routes())
    assert daemon_api_digest() == expected
    assert len(expected) == 64


def test_the_pin_is_checked_before_anything_is_hashed(tmp_path: Path) -> None:
    # A daemon that just updated finds a stale overlay on every start, and that
    # start must not cost a full hash pass over a tree it is going to refuse.
    tree = install_tree(make_tree(tmp_path / "static"), requires_backend="0.0.1")
    (tree / "assets" / "index-deadbeef.js").unlink()
    verdict = verify_tree(tree, backend_version=BACKEND, api_digest=API)
    # `missing_file` would mean the file walk ran; the pin has to win.
    assert verdict.reason == "version_mismatch"


# --- resolution ---------------------------------------------------------------------


def test_no_overlay_resolves_to_the_bundled_tree(tmp_path: Path) -> None:
    bundled = make_tree(tmp_path / "bundled")
    choice = resolve_frontend_dir(
        data_dir=tmp_path / "data", bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    assert choice.directory == bundled
    assert choice.source == "bundled"
    assert choice.reason == "no_overlay"
    assert not choice.faulted


def test_a_verified_overlay_is_preferred_over_the_bundled_tree(tmp_path: Path) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    assert choice.source == "overlay"
    assert choice.directory != bundled
    assert choice.directory.is_relative_to(data)
    assert (choice.directory / "index.html").is_file()


def test_a_broken_overlay_resolves_to_the_bundle_and_says_why(tmp_path: Path) -> None:
    # The safety property the whole design rests on: a bad overlay costs a stale
    # frontend and a log line, never a daemon that will not start.
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    victim = tree_path(data, result.digest) / "assets" / "index-deadbeef.js"
    victim.write_text("// corrupted after install\n")
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    assert choice.source == "bundled"
    assert choice.directory == bundled
    assert choice.reason == "hash_mismatch"
    assert choice.faulted


def test_a_pin_mismatch_resolves_to_the_bundle_and_is_reported_as_a_fault(
    tmp_path: Path,
) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    OverlayStore(data, backend_version=BACKEND, api_digest=API).install_from_directory(
        install_tree(make_tree(tmp_path / "built"))
    )
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version="1.0.0", api_digest=API
    )
    assert choice.source == "bundled"
    assert choice.reason == "version_mismatch"
    assert choice.faulted


def test_a_missing_tree_directory_resolves_to_the_bundle(tmp_path: Path) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    import shutil

    shutil.rmtree(tree_path(data, result.digest))
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    assert choice.source == "bundled"
    assert choice.reason == "tree_missing"


def test_the_install_wide_switch_serves_the_bundle_without_uninstalling_anything(
    tmp_path: Path,
) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API, enabled=False
    )
    assert choice.source == "bundled"
    assert choice.reason == "disabled"
    assert not choice.faulted
    # Off is not uninstalled: turning it back on has to find the overlay intact.
    assert store.state.active
    assert store.status()["installed"]


def test_an_unreadable_state_file_resolves_to_the_bundle_rather_than_raising(
    tmp_path: Path,
) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    path = state_path(data)
    path.parent.mkdir(parents=True)
    path.write_text("{ truncated", encoding="utf-8")
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    assert choice.source == "bundled"
    assert choice.reason == "no_overlay"


# --- installing ----------------------------------------------------------------------


def test_installing_a_directory_copies_it_and_leaves_the_source_alone(tmp_path: Path) -> None:
    data = tmp_path / "data"
    source = install_tree(make_tree(tmp_path / "built"))
    result = OverlayStore(data, backend_version=BACKEND, api_digest=API).install_from_directory(
        source
    )
    installed = tree_path(data, result.digest)
    assert installed.is_dir()
    assert (installed / "index.html").read_text(encoding="utf-8") == INDEX
    assert source.is_dir()
    assert (source / "index.html").is_file()


def test_installing_a_payload_with_no_manifest_is_refused(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(OverlayRefused) as refusal:
        OverlayStore(data, backend_version=BACKEND, api_digest=API).install_from_directory(
            make_tree(tmp_path / "built")
        )
    assert refusal.value.reason == "manifest_missing"
    assert not OverlayStore(data, backend_version=BACKEND, api_digest=API).status()["installed"]


def test_installing_a_payload_pinned_elsewhere_is_refused_at_install_time(
    tmp_path: Path,
) -> None:
    # Refused on the way in as well as at resolution: installing a pairing that
    # can never be served would be a success message about nothing.
    data = tmp_path / "data"
    source = install_tree(make_tree(tmp_path / "built"), requires_backend="0.0.1")
    with pytest.raises(OverlayRefused) as refusal:
        OverlayStore(data, backend_version=BACKEND, api_digest=API).install_from_directory(source)
    assert refusal.value.reason == "version_mismatch"


def test_a_refused_install_leaves_a_working_overlay_untouched(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    good = store.install_from_directory(install_tree(make_tree(tmp_path / "good")))
    bad = install_tree(make_tree(tmp_path / "bad", extra={"extra.js": "x"}))
    (bad / "extra.js").write_text("changed after the manifest was written")
    with pytest.raises(OverlayRefused):
        store.install_from_directory(bad)
    assert store.state.digest == good.digest
    assert store.state.active
    bundled = make_tree(tmp_path / "bundled")
    assert (
        resolve_frontend_dir(
            data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
        ).source
        == "overlay"
    )


def test_an_archive_round_trips_through_pack_and_install(tmp_path: Path) -> None:
    source = make_tree(tmp_path / "built")
    manifest = build_manifest(source, requires_backend=BACKEND, requires_api=API)
    archive = pack_overlay(source, tmp_path / "out" / "ui.zip", manifest)
    data = tmp_path / "data"
    result = OverlayStore(data, backend_version=BACKEND, api_digest=API).install_from_archive(
        archive
    )
    assert result.digest == manifest.tree_digest
    assert (tree_path(data, result.digest) / "index.html").read_text(encoding="utf-8") == INDEX
    # The manifest went into the archive, not into the checkout's static tree.
    assert not (source / MANIFEST_NAME).exists()


def test_an_archive_whose_digest_does_not_match_is_refused(tmp_path: Path) -> None:
    source = install_tree(make_tree(tmp_path / "built"))
    archive = pack_overlay(source, tmp_path / "out" / "ui.zip")
    with pytest.raises(OverlayRefused) as refusal:
        OverlayStore(
            tmp_path / "data", backend_version=BACKEND, api_digest=API
        ).install_from_archive(archive, sha256="0" * 64)
    assert refusal.value.reason == "payload_hash_mismatch"


def test_an_archive_that_would_write_outside_its_own_tree_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr(f"{ARCHIVE_ROOT}/index.html", INDEX)
        payload.writestr("../escaped.js", "evil()")
    with pytest.raises(OverlayRefused) as refusal:
        extract_overlay(archive, tmp_path / "staging")
    assert refusal.value.reason == "archive_invalid"


def test_an_archive_not_rooted_at_static_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("swe-mux/index.html", INDEX)
    with pytest.raises(OverlayRefused) as refusal:
        extract_overlay(archive, tmp_path / "staging")
    assert refusal.value.reason == "archive_invalid"


def test_a_file_named_zip_that_is_not_one_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "not-really.zip"
    archive.write_bytes(b"this is not a zip file")
    with pytest.raises(OverlayRefused) as refusal:
        extract_overlay(archive, tmp_path / "staging")
    assert refusal.value.reason == "archive_invalid"


def test_installing_the_same_tree_twice_is_idempotent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    source = install_tree(make_tree(tmp_path / "built"))
    first = store.install_from_directory(source)
    second = store.install_from_directory(source)
    assert first.digest == second.digest
    assert second.already_serving
    assert store.state.digest == first.digest


def test_a_second_install_keeps_the_previous_tree_and_prunes_the_one_before(
    tmp_path: Path,
) -> None:
    # Two generations, so the tree an overlay is reverted *from* is still on disk
    # to inspect - and no more than two, because these are tens of megabytes.
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    digests = []
    for index in range(3):
        source = install_tree(make_tree(tmp_path / f"built{index}", extra={"v.js": str(index)}))
        digests.append(store.install_from_directory(source).digest)
    assert tree_path(data, digests[2]).is_dir()
    assert tree_path(data, digests[1]).is_dir()
    assert not tree_path(data, digests[0]).exists()


def test_installing_leaves_no_staging_directory_behind(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    staging = store.root / "staging"
    assert not staging.exists() or not any(staging.iterdir())


# --- download ------------------------------------------------------------------------


def _downloader(body: bytes, *, status: int = 200, declared: int | None = None):
    async def download(url, *, write, max_bytes):
        del url
        for start in range(0, max(len(body), 1), 4096):
            write(body[start : start + 4096])
        return status, len(body) if declared is None else declared

    return download


@pytest.mark.asyncio
async def test_a_download_installs_only_when_its_digest_matches(tmp_path: Path) -> None:
    source = install_tree(make_tree(tmp_path / "built"))
    archive = pack_overlay(source, tmp_path / "out" / "ui.zip")
    body = archive.read_bytes()
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API, download=_downloader(body))
    result = await store.install_from_url(
        "https://example.invalid/ui.zip", sha256=file_digest(archive)
    )
    assert tree_path(data, result.digest).is_dir()


@pytest.mark.asyncio
async def test_a_download_that_does_not_match_its_digest_installs_nothing(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    store = OverlayStore(
        data, backend_version=BACKEND, api_digest=API, download=_downloader(b"tampered")
    )
    with pytest.raises(OverlayRefused) as refusal:
        await store.install_from_url("https://example.invalid/ui.zip", sha256="a" * 64)
    assert refusal.value.reason == "payload_hash_mismatch"
    assert not store.status()["installed"]
    # And nothing is left under a name a later install could mistake for verified.
    assert not list(store.downloads_dir.glob("*.zip"))


@pytest.mark.asyncio
async def test_a_download_without_a_digest_is_refused_before_a_byte_is_fetched(
    tmp_path: Path,
) -> None:
    fetched = False

    async def download(url, *, write, max_bytes):
        nonlocal fetched
        fetched = True
        return 200, 0

    store = OverlayStore(
        tmp_path / "data", backend_version=BACKEND, api_digest=API, download=download
    )
    with pytest.raises(OverlayRefused) as refusal:
        await store.install_from_url("https://example.invalid/ui.zip", sha256="")
    assert refusal.value.reason == "digest_required"
    assert not fetched


@pytest.mark.asyncio
async def test_a_truncated_download_is_distinguished_from_a_bad_hash(tmp_path: Path) -> None:
    store = OverlayStore(
        tmp_path / "data",
        backend_version=BACKEND,
        api_digest=API,
        download=_downloader(b"partial", declared=9999),
    )
    with pytest.raises(OverlayRefused) as refusal:
        await store.install_from_url("https://example.invalid/ui.zip", sha256="a" * 64)
    assert refusal.value.reason == "truncated"


@pytest.mark.asyncio
async def test_a_non_200_download_is_reported_as_unreachable(tmp_path: Path) -> None:
    store = OverlayStore(
        tmp_path / "data",
        backend_version=BACKEND,
        api_digest=API,
        download=_downloader(b"", status=404),
    )
    with pytest.raises(OverlayRefused) as refusal:
        await store.install_from_url("https://example.invalid/ui.zip", sha256="a" * 64)
    assert refusal.value.reason == "unreachable"


# --- the revert -----------------------------------------------------------------------


def test_reverting_serves_the_bundle_again_without_removing_the_overlay(
    tmp_path: Path,
) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    assert (
        resolve_frontend_dir(
            data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
        ).source
        == "overlay"
    )

    answer = store.revert()
    assert answer["changed"]
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    assert choice.source == "bundled"
    assert choice.reason == "reverted"
    # A revert is not a fault: nothing broke, somebody chose this.
    assert not choice.faulted
    # And the tree is still there, so restoring is the same press in reverse and
    # the bytes are still available when the question is *why* it was wrong.
    assert tree_path(data, result.digest).is_dir()


def test_reverting_touches_only_the_state_file(tmp_path: Path) -> None:
    # The property that makes a revert unable to half-fail: it is one atomic
    # write of a few hundred bytes and it moves, deletes and rewrites nothing.
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    root = tree_path(data, result.digest)
    before = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
    store.revert()
    after = {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
    assert before == after


def test_reverting_twice_is_not_an_error(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    assert store.revert()["changed"]
    second = store.revert()
    assert second["reverted"] and not second["changed"]


def test_reverting_with_nothing_installed_says_so(tmp_path: Path) -> None:
    with pytest.raises(OverlayRefused) as refusal:
        OverlayStore(tmp_path / "data", backend_version=BACKEND, api_digest=API).revert()
    assert refusal.value.reason == "nothing_installed"


def test_restoring_puts_a_reverted_overlay_back(tmp_path: Path) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    store.revert()
    assert store.status()["can_restore"]
    assert store.restore()["changed"]
    assert (
        resolve_frontend_dir(
            data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
        ).source
        == "overlay"
    )


def test_restoring_an_overlay_whose_files_are_gone_is_refused(tmp_path: Path) -> None:
    import shutil

    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    store.revert()
    shutil.rmtree(tree_path(data, result.digest))
    with pytest.raises(OverlayRefused) as refusal:
        store.restore()
    assert refusal.value.reason == "tree_missing"


def test_installing_after_a_revert_switches_the_overlay_back_on(tmp_path: Path) -> None:
    # Installing is an act of intent, so it implies "and use it"; leaving a fresh
    # install switched off because a previous one was reverted would be a silent
    # no-op of exactly the kind this feature exists to end.
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "old")))
    store.revert()
    store.install_from_directory(install_tree(make_tree(tmp_path / "new", extra={"v.js": "2"})))
    assert (
        resolve_frontend_dir(
            data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
        ).source
        == "overlay"
    )


# --- status -----------------------------------------------------------------------------


def test_status_reports_what_is_installed_and_what_is_served(tmp_path: Path) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    choice = resolve_frontend_dir(
        data_dir=data, bundled=bundled, backend_version=BACKEND, api_digest=API
    )
    payload = store.status(choice)
    assert payload["installed"] and payload["active"] and payload["tree_exists"]
    assert payload["state"]["digest"] == result.digest
    assert payload["state"]["requires_backend"] == BACKEND
    assert payload["serving"]["serving"] == "overlay"
    assert payload["serving"]["overlay"]["file_count"] == 3
    # The file map is not in a summary: it is a hundred lines nobody reads.
    assert "files" not in payload["serving"]["overlay"]


def test_status_distinguishes_a_faulted_overlay_from_an_absent_one(tmp_path: Path) -> None:
    bundled = make_tree(tmp_path / "bundled")
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    faulted = store.status(
        resolve_frontend_dir(
            data_dir=data, bundled=bundled, backend_version="1.0.0", api_digest=API
        )
    )
    assert faulted["installed"]
    assert faulted["serving"]["faulted"]
    absent = OverlayStore(tmp_path / "empty", backend_version=BACKEND, api_digest=API).status(
        resolve_frontend_dir(
            data_dir=tmp_path / "empty", bundled=bundled, backend_version=BACKEND, api_digest=API
        )
    )
    assert not absent["installed"]
    assert not absent["serving"]["faulted"]


def test_the_state_file_survives_a_round_trip_through_disk(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = OverlayStore(data, backend_version=BACKEND, api_digest=API)
    result = store.install_from_directory(install_tree(make_tree(tmp_path / "built")))
    payload = json.loads(state_path(data).read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["digest"] == result.digest
    assert payload["active"] is True
    assert OverlayStore(data, backend_version=BACKEND, api_digest=API).state.digest == result.digest
