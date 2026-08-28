"""The in-app update check: comparison, manifest handling, the interval, the route.

The comparison is where the weight is. Everything else in this feature degrades
to "unknown" when it is wrong, but a wrong comparison is confidently wrong: it
either tells an operator they are current when they are three releases behind, or
it shows a banner for a version they are already running. A string compare does
both - `"0.10.0" < "0.9.0"` and `"0.1.0" != "0.1.0"` after a `v` prefix creeps in
- which is why the parser is pure and pinned here rather than inlined in the
service.

The second thing pinned here is the promise the README makes: **off means no
request**. That is asserted by counting fetches through an injected fetcher, so
the claim is measured rather than reasoned about.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.routes import update as update_routes
from swe_mux.update_check import (
    INCOMPARABLE,
    MALFORMED,
    OK,
    UNREACHABLE,
    UNSUPPORTED_SCHEMA,
    UpdateChecker,
    compare_versions,
    is_newer,
    parse_github_release,
    parse_manifest,
    parse_version,
)

# --- version comparison ------------------------------------------------------


def test_a_numeric_segment_is_compared_as_a_number() -> None:
    # The failure a string compare makes and never recovers from: the tenth
    # minor release sorts below the ninth for the rest of the project's life.
    assert is_newer("0.10.0", "0.9.0") is True
    assert is_newer("0.9.0", "0.10.0") is False
    assert is_newer("1.0.0", "0.99.99") is True
    assert compare_versions("0.10.0", "0.9.0") == 1


def test_a_version_is_not_newer_than_itself() -> None:
    # The other half of the same coin: an equal version must never raise a
    # banner, however it is spelled.
    assert is_newer("0.1.0", "0.1.0") is False
    assert compare_versions("0.1.0", "0.1.0") == 0
    # A `v` prefix is what a tag carries and a package version does not, and the
    # two are compared against each other constantly.
    assert compare_versions("v0.1.0", "0.1.0") == 0
    # Trailing zeros are not a difference; nobody typed the third segment.
    assert compare_versions("1.0", "1.0.0") == 0
    assert compare_versions("1.0", "1.0.0.0") == 0
    assert is_newer(" 0.1.0 ", "0.1.0") is False


def test_a_prerelease_sorts_below_its_own_release() -> None:
    assert is_newer("1.0.0rc1", "1.0.0") is False
    assert is_newer("1.0.0", "1.0.0rc1") is True
    # ...and above the release before it, which is what makes an rc offerable.
    assert is_newer("1.0.0rc1", "0.9.0") is True
    # a < b < rc, and the numbers inside a label order too.
    assert compare_versions("1.0.0a1", "1.0.0b1") == -1
    assert compare_versions("1.0.0b1", "1.0.0rc1") == -1
    assert compare_versions("1.0.0a2", "1.0.0a10") == -1
    # The spelled-out labels a hand-written tag uses mean the same thing.
    assert compare_versions("1.0.0alpha1", "1.0.0a1") == 0
    assert compare_versions("1.0.0beta1", "1.0.0b1") == 0


def test_dev_and_post_releases_sort_where_pep_440_puts_them() -> None:
    # dev is below everything it is a dev of, including a prerelease.
    assert compare_versions("1.0.0.dev1", "1.0.0a1") == -1
    assert compare_versions("1.0.0a1.dev1", "1.0.0a1") == -1
    assert compare_versions("1.0.0.dev1", "1.0.0") == -1
    # post is above the release it is a post of.
    assert compare_versions("1.0.0.post1", "1.0.0") == 1
    assert compare_versions("1.0.0.post1.dev1", "1.0.0.post1") == -1
    assert compare_versions("1.0.0-1", "1.0.0") == 1
    # A bare label carries an implicit zero rather than meaning "absent".
    assert compare_versions("1.0.0.post", "1.0.0") == 1
    assert compare_versions("1.0.0.dev", "1.0.0") == -1


def test_a_local_segment_does_not_change_the_release_it_names() -> None:
    # `uv build` from a dirty tree emits one; it says nothing about which
    # release is newer, so it must not make a build look ahead of the tag.
    assert compare_versions("1.0.0+local", "1.0.0") == 0
    assert is_newer("1.0.0+local", "0.9.0") is True


def test_an_unreadable_version_answers_cannot_tell_rather_than_older() -> None:
    # `None`, never `False`. Folding it into `False` would make a build whose own
    # version does not parse claim to be current forever.
    assert parse_version("not-a-version") is None
    assert parse_version("") is None
    assert parse_version(None) is None
    assert parse_version(17) is None
    assert compare_versions("nonsense", "1.0.0") is None
    assert is_newer("nonsense", "1.0.0") is None
    assert is_newer("1.0.0", "nonsense") is None


# --- manifest parsing --------------------------------------------------------

MANIFEST = {
    "schema": 1,
    "version": "0.2.0",
    "tag": "v0.2.0",
    "published": "2026-08-27T00:00:00Z",
    "changelog": "https://github.com/jatoran/swe-mux/releases/tag/v0.2.0",
    "artifacts": [{"name": "w.whl", "url": "https://example.invalid/w.whl", "sha256": "ab"}],
}


def test_a_current_manifest_reduces_to_the_four_fields_a_banner_needs() -> None:
    release, reason = parse_manifest(MANIFEST)
    assert reason == OK
    assert release is not None
    assert release.version == "0.2.0"
    assert release.tag == "v0.2.0"
    assert release.changelog.endswith("/tag/v0.2.0")
    assert release.source == "manifest"


def test_an_unrecognized_schema_is_refused_without_reading_a_field() -> None:
    # The whole point of the version field. A future manifest may repurpose
    # `version`, so a build that reads it anyway is guessing, and the honest
    # answer for a three-year-old install is "cannot tell".
    future = {**MANIFEST, "schema": 2, "version": "9.9.9"}
    release, reason = parse_manifest(future)
    assert release is None
    assert reason == UNSUPPORTED_SCHEMA


def test_a_manifest_with_no_schema_is_refused_rather_than_assumed_current() -> None:
    # An absent `schema` is not "schema 1 with the field left off": the contract
    # has always carried it, so its absence means this is not the document the
    # parser was written against, and guessing would be the same mistake as
    # reading a future one.
    release, reason = parse_manifest({k: v for k, v in MANIFEST.items() if k != "schema"})
    assert (release, reason) == (None, UNSUPPORTED_SCHEMA)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "a string",
        [],
        {"schema": 1},
        {"schema": 1, "version": ""},
        {"schema": 1, "version": "   "},
        {"schema": 1, "version": 3},
    ],
)
def test_a_malformed_manifest_never_produces_a_release(payload: object) -> None:
    release, reason = parse_manifest(payload)
    assert release is None
    assert reason in {MALFORMED, UNSUPPORTED_SCHEMA}


def test_a_manifest_missing_optional_fields_still_answers() -> None:
    # `version` is the only field the comparison needs. A tag and a changelog
    # link are presentation, and their absence must not throw the answer away.
    release, reason = parse_manifest({"schema": 1, "version": "0.2.0"})
    assert reason == OK
    assert release is not None
    assert (release.tag, release.changelog, release.published) == ("v0.2.0", "", "")


def test_the_github_fallback_reads_the_tag_and_the_release_page() -> None:
    release, reason = parse_github_release(
        {
            "tag_name": "v0.3.0",
            "html_url": "https://github.com/jatoran/swe-mux/releases/tag/v0.3.0",
            "published_at": "2026-09-01T00:00:00Z",
            "draft": False,
            "prerelease": False,
        }
    )
    assert reason == OK
    assert release is not None
    assert (release.version, release.tag, release.source) == ("0.3.0", "v0.3.0", "github")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"tag_name": ""},
        {"tag_name": "v"},
        {"tag_name": 5},
        # A draft is not published, so announcing it would be worse than silence.
        {"tag_name": "v0.3.0", "draft": True},
    ],
)
def test_a_github_payload_that_is_not_a_published_release_answers_nothing(payload: object) -> None:
    release, reason = parse_github_release(payload)
    assert (release, reason) == (None, MALFORMED)


# --- the checker -------------------------------------------------------------


class FakeFetch:
    """A fetcher that counts, so "no request" is measured rather than asserted.

    Keyed by URL so the manifest and the GitHub fallback can be given different
    answers in one test, which is the only way to exercise the fallback at all.
    """

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []
        self.headers: list[dict[str, str]] = []

    async def __call__(self, url: str, *, headers: Any = None) -> tuple[int, bytes]:
        self.calls.append(url)
        self.headers.append(dict(headers or {}))
        answer = self.answers.get(url)
        if answer is None:
            raise OSError("unreachable")
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            status, body = answer
            return status, body if isinstance(body, bytes) else json.dumps(body).encode()
        return 200, json.dumps(answer).encode()


MANIFEST_URL = "https://manifest.invalid/version.json"
GITHUB_URL = "https://api.invalid/releases/latest"


class Clock:
    """A wall clock the test moves, because the interval is a wall-clock fact."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def build(
    tmp_path: Path,
    fetch: FakeFetch,
    *,
    clock: Clock | None = None,
    enabled: bool = True,
    current: str = "0.1.0",
    interval: float = 3600.0,
) -> UpdateChecker:
    config = Config(data_dir=tmp_path)
    config.update_check_enabled = enabled
    return UpdateChecker(
        config,
        current_version=current,
        manifest_url=MANIFEST_URL,
        github_url=GITHUB_URL,
        interval_seconds=interval,
        fetch=fetch,
        clock=clock or Clock(),
    )


async def test_a_newer_release_raises_a_banner(tmp_path: Path) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch)
    snapshot = await checker.check()
    assert fetch.calls == [MANIFEST_URL]
    assert snapshot["status"] == OK
    assert snapshot["update_available"] is True
    assert snapshot["banner"] is True
    assert snapshot["latest"]["version"] == "0.2.0"
    assert snapshot["current_version"] == "0.1.0"


async def test_the_running_version_being_the_latest_raises_nothing(tmp_path: Path) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, current="0.2.0")
    snapshot = await checker.check()
    assert snapshot["status"] == OK
    assert (snapshot["update_available"], snapshot["banner"]) == (False, False)


async def test_a_running_version_ahead_of_the_manifest_raises_nothing(tmp_path: Path) -> None:
    # The developer case, and the one a naive "different means newer" check gets
    # backwards while showing a banner offering a downgrade.
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, current="0.3.0")
    snapshot = await checker.check()
    assert (snapshot["update_available"], snapshot["banner"]) == (False, False)


async def test_a_disabled_check_makes_no_request_at_all(tmp_path: Path) -> None:
    # The README's no-telemetry claim, measured. Every entry point is tried: the
    # passive read, the interval-driven check, and the explicit force - none of
    # them may put a byte on the wire.
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, enabled=False)
    await checker.ensure_loaded()
    assert checker.snapshot()["status"] == "disabled"
    await checker.check()
    await checker.check(force=True)
    assert fetch.calls == []
    assert not (tmp_path / "update-check.json").exists()


async def test_turning_the_check_off_stops_it_without_a_restart(tmp_path: Path) -> None:
    # `runtime_config` mutates the live `Config` in place, so the switch has to
    # be read at each check rather than captured at construction.
    clock = Clock()
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, clock=clock)
    await checker.check()
    assert len(fetch.calls) == 1
    checker._config.update_check_enabled = False
    clock.now += 10_000
    await checker.check()
    await checker.check(force=True)
    assert len(fetch.calls) == 1
    assert checker.snapshot()["status"] == "disabled"


async def test_at_most_one_check_per_interval(tmp_path: Path) -> None:
    clock = Clock()
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, clock=clock, interval=3600.0)
    await checker.check()
    await checker.check()
    clock.now += 3599.0
    await checker.check()
    assert len(fetch.calls) == 1
    clock.now += 2.0
    await checker.check()
    assert len(fetch.calls) == 2


async def test_a_forced_check_skips_the_interval_but_not_the_switch(tmp_path: Path) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch)
    await checker.check()
    await checker.check(force=True)
    assert len(fetch.calls) == 2


async def test_the_interval_survives_a_restart(tmp_path: Path) -> None:
    # The property that stops a restart loop becoming a request loop: the
    # timestamp is on disk, so a fresh process inherits the last check rather
    # than starting a new day.
    clock = Clock()
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    await build(tmp_path, fetch, clock=clock).check()
    assert len(fetch.calls) == 1
    for _ in range(5):
        successor = build(tmp_path, fetch, clock=clock)
        await successor.check()
    assert len(fetch.calls) == 1
    # ...and the previous answer comes back with it, so a restart does not blank
    # a banner the operator has already seen.
    assert successor.snapshot()["latest"]["version"] == "0.2.0"


async def test_a_clock_that_moved_backwards_makes_the_check_due(tmp_path: Path) -> None:
    # The safe direction: one extra request, rather than silently never checking
    # again until wall-clock time catches up with a timestamp from the future.
    clock = Clock()
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, clock=clock)
    await checker.check()
    clock.now -= 86_400.0
    await checker.check()
    assert len(fetch.calls) == 2


async def test_an_unreachable_manifest_falls_back_to_github(tmp_path: Path) -> None:
    fetch = FakeFetch(
        {
            GITHUB_URL: {
                "tag_name": "v0.4.0",
                "html_url": "https://example.invalid/tag/v0.4.0",
                "published_at": "2026-09-01T00:00:00Z",
            }
        }
    )
    checker = build(tmp_path, fetch)
    snapshot = await checker.check()
    assert fetch.calls == [MANIFEST_URL, GITHUB_URL]
    assert snapshot["status"] == OK
    assert snapshot["latest"] == {
        "version": "0.4.0",
        "tag": "v0.4.0",
        "published": "2026-09-01T00:00:00Z",
        "changelog": "https://example.invalid/tag/v0.4.0",
        "source": "github",
    }
    # Content negotiation only: nothing in the request identifies this install.
    assert fetch.headers[0] == {}
    assert fetch.headers[1] == {"Accept": "application/vnd.github+json"}


async def test_a_schema_this_build_cannot_read_also_falls_back(tmp_path: Path) -> None:
    # A manifest we cannot read and a manifest that is not there are the same
    # fact from here, and the release list answers the question either way.
    fetch = FakeFetch(
        {
            MANIFEST_URL: {**MANIFEST, "schema": 99},
            GITHUB_URL: {"tag_name": "v0.4.0", "html_url": "https://example.invalid/t"},
        }
    )
    snapshot = await build(tmp_path, fetch).check()
    assert snapshot["status"] == OK
    assert snapshot["latest"]["version"] == "0.4.0"


async def test_both_sources_failing_is_a_logged_non_event(tmp_path: Path) -> None:
    fetch = FakeFetch({})
    snapshot = await build(tmp_path, fetch).check()
    assert fetch.calls == [MANIFEST_URL, GITHUB_URL]
    assert snapshot["status"] == UNREACHABLE
    assert (snapshot["update_available"], snapshot["banner"]) == (False, False)
    assert snapshot["latest"] is None


async def test_the_manifests_own_reason_survives_a_failed_fallback(tmp_path: Path) -> None:
    # The reason reported is the one the operator can act on: the site said
    # something unreadable, which is not the same as the site being down.
    fetch = FakeFetch({MANIFEST_URL: (200, b"<html>not json</html>")})
    snapshot = await build(tmp_path, fetch).check()
    assert snapshot["status"] == MALFORMED


@pytest.mark.parametrize(
    "answer",
    [
        (404, b"{}"),
        (500, b"{}"),
        (200, b"<html>captive portal</html>"),
        (200, b""),
        OSError("dns"),
        TimeoutError(),
    ],
)
async def test_no_transport_failure_escapes_as_an_exception(
    tmp_path: Path, answer: object
) -> None:
    fetch = FakeFetch({MANIFEST_URL: answer, GITHUB_URL: answer})
    snapshot = await build(tmp_path, fetch).check()
    assert snapshot["status"] in {UNREACHABLE, MALFORMED}
    assert snapshot["update_available"] is False


async def test_a_check_that_succeeded_with_an_uncomparable_version_says_so(
    tmp_path: Path,
) -> None:
    # The fetch worked and the schema was right; the version string was not one.
    # That is a different fact from "the site is down" and must not read as it.
    fetch = FakeFetch({MANIFEST_URL: {"schema": 1, "version": "banana"}})
    snapshot = await build(tmp_path, fetch).check()
    assert snapshot["status"] == INCOMPARABLE
    assert (snapshot["update_available"], snapshot["banner"]) == (False, False)


async def test_a_declined_version_stays_declined_across_a_restart(tmp_path: Path) -> None:
    clock = Clock()
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, clock=clock)
    assert (await checker.check())["banner"] is True
    snapshot = await checker.dismiss("0.2.0")
    assert (snapshot["update_available"], snapshot["banner"]) == (True, False)
    successor = build(tmp_path, fetch, clock=clock)
    await successor.ensure_loaded()
    assert successor.snapshot()["banner"] is False
    assert successor.snapshot()["dismissed"] == ["0.2.0"]


async def test_declining_one_version_does_not_decline_the_next(tmp_path: Path) -> None:
    # The whole difference between dismissing an update and turning the feature
    # off. Getting this wrong silently ends the feature at the first dismissal.
    clock = Clock()
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch, clock=clock)
    await checker.check()
    await checker.dismiss("0.2.0")
    fetch.answers[MANIFEST_URL] = {**MANIFEST, "version": "0.3.0", "tag": "v0.3.0"}
    clock.now += 10_000
    snapshot = await checker.check()
    assert snapshot["latest"]["version"] == "0.3.0"
    assert snapshot["banner"] is True


async def test_dismissing_nothing_is_a_no_op(tmp_path: Path) -> None:
    checker = build(tmp_path, FakeFetch({}))
    snapshot = await checker.dismiss("   ")
    assert snapshot["dismissed"] == []


async def test_a_corrupt_state_file_starts_from_empty_rather_than_failing(
    tmp_path: Path,
) -> None:
    (tmp_path / "update-check.json").write_text("{not json", encoding="utf-8")
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch)
    snapshot = await checker.check()
    assert snapshot["status"] == OK
    assert len(fetch.calls) == 1


async def test_a_state_file_from_a_future_build_starts_from_empty(tmp_path: Path) -> None:
    (tmp_path / "update-check.json").write_text(
        json.dumps({"schema": 99, "last_checked": 10.0}), encoding="utf-8"
    )
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch)
    await checker.check()
    # The unreadable timestamp did not suppress the check.
    assert len(fetch.calls) == 1


async def test_the_state_file_is_readable_and_carries_what_it_promises(
    tmp_path: Path,
) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch)
    await checker.check()
    await checker.dismiss("0.2.0")
    payload = json.loads((tmp_path / "update-check.json").read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["status"] == OK
    assert payload["dismissed"] == ["0.2.0"]
    assert payload["latest"]["version"] == "0.2.0"
    assert isinstance(payload["last_checked"], (int, float))


async def test_a_never_checked_install_says_so_rather_than_up_to_date(
    tmp_path: Path,
) -> None:
    checker = build(tmp_path, FakeFetch({}))
    await checker.ensure_loaded()
    snapshot = checker.snapshot()
    assert snapshot["status"] == "never_checked"
    assert (snapshot["checked_at"], snapshot["next_check_at"]) == (None, None)


# --- the route ---------------------------------------------------------------


def route_app(checker: UpdateChecker | None) -> web.Application:
    app = web.Application()
    if checker is not None:
        app[keys.UPDATE_CHECK] = checker
    app.add_routes(update_routes.ROUTES)
    return app


async def test_reading_the_endpoint_never_reaches_the_network(tmp_path: Path) -> None:
    # The reason GET and POST are separate handlers: a phone that polls this,
    # or a page that mounts the banner on every load, must cost nothing.
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    client = TestClient(TestServer(route_app(build(tmp_path, fetch))))
    await client.start_server()
    try:
        for _ in range(3):
            response = await client.get("/api/update")
            assert response.status == 200
            assert (await response.json())["status"] == "never_checked"
        assert fetch.calls == []
    finally:
        await client.close()


async def test_checking_now_requires_an_explicit_user_action(tmp_path: Path) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    client = TestClient(TestServer(route_app(build(tmp_path, fetch))))
    await client.start_server()
    try:
        refused = await client.post("/api/update/check")
        assert refused.status == 400
        assert fetch.calls == []
        accepted = await client.post(
            "/api/update/check", headers={"X-Mux-User-Gesture": "update-check"}
        )
        assert accepted.status == 200
        assert (await accepted.json())["banner"] is True
        assert fetch.calls == [MANIFEST_URL]
    finally:
        await client.close()


async def test_checking_now_is_refused_while_the_switch_is_off(tmp_path: Path) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    client = TestClient(TestServer(route_app(build(tmp_path, fetch, enabled=False))))
    await client.start_server()
    try:
        response = await client.post(
            "/api/update/check", headers={"X-Mux-User-Gesture": "update-check"}
        )
        assert response.status == 409
        assert (await response.json())["error"] == "update_check_disabled"
        assert fetch.calls == []
    finally:
        await client.close()


async def test_dismissing_over_the_route_persists_and_needs_a_version(
    tmp_path: Path,
) -> None:
    fetch = FakeFetch({MANIFEST_URL: MANIFEST})
    checker = build(tmp_path, fetch)
    client = TestClient(TestServer(route_app(checker)))
    await client.start_server()
    try:
        await checker.check()
        empty = await client.post("/api/update/dismiss", json={})
        assert empty.status == 400
        done = await client.post("/api/update/dismiss", json={"version": "0.2.0"})
        assert done.status == 200
        assert (await done.json())["banner"] is False
        assert (await (await client.get("/api/update")).json())["banner"] is False
    finally:
        await client.close()


async def test_a_daemon_without_an_update_checker_answers_quietly() -> None:
    # A partially-built runtime is reachable (the listener binds before the
    # runtime exists), and a banner endpoint that 500s there would put a network
    # error in front of someone who did not ask about the network.
    client = TestClient(TestServer(route_app(None)))
    await client.start_server()
    try:
        for path, method in (("/api/update", "get"), ("/api/update/check", "post")):
            response = await getattr(client, method)(path)
            assert response.status == 200
            assert (await response.json())["banner"] is False
    finally:
        await client.close()
