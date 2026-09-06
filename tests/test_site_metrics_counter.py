"""The site's one counter records what the privacy page says it records, and no more.

`worker/index.js` counts requests to `swemux.dev/version.json` - the daily poll every
install makes, and the only adoption signal a project with no telemetry has. The counter
is nine lines and its whole schema is a constant label plus an HTTP status. That schema
is a **published promise**: `site/content/privacy.html` tells readers, in those words,
that no address, user agent, country, referer, or identifier derived from any of them is
written down, and invites them to check.

A promise like that rots in one of two ways, and neither is visible in a green run or in
a working site:

**The counter quietly grows a field.** Adding `request.headers.get('cf-connecting-ip')`
to the data point is one line, it would work, it would be useful, and it would make the
privacy page false the moment it deployed. Tests are how a claim about source becomes a
claim the build enforces, so this reads the call and fails on anything derived from the
request.

**The counter quietly stops counting.** With Workers Assets, a path that matches a file
is served without the script running at all, so `assets.run_worker_first` in
`wrangler.jsonc` is the only reason the Worker sees `/version.json`. Change the path in
one file and not the other and the site serves perfectly, the deploy succeeds, and the
number is zero forever - a failure whose only symptom is a metric that looks like nobody
uses the software. The same is true of the dataset name and label
`tools/metrics_snapshot.py` queries with.

Everything here reads committed source. It makes no network request and needs no
Cloudflare account.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from swe_mux.project_actions import loads_jsonc

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "worker" / "index.js"
WRANGLER = REPO_ROOT / "wrangler.jsonc"
SNAPSHOT = REPO_ROOT / "tools" / "metrics_snapshot.py"
PRIVACY = REPO_ROOT / "site" / "content" / "privacy.html"


def _worker_source() -> str:
    return WORKER.read_text(encoding="utf-8")


def _wrangler() -> dict[str, Any]:
    parsed = loads_jsonc(WRANGLER.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _js_const(name: str) -> str:
    """The value of a top-level `const NAME = '...'` in the Worker."""
    match = re.search(rf"^const {name} = '([^']*)'", _worker_source(), re.MULTILINE)
    assert match, f"{name} is no longer a single-quoted top-level const in {WORKER.name}"
    return match.group(1)


def _py_const(name: str) -> str:
    """The value of a module-level `NAME = "..."` in the snapshot script."""
    match = re.search(rf'^{name} = "([^"]*)"', SNAPSHOT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{name} is no longer a double-quoted module constant in {SNAPSHOT.name}"
    return match.group(1)


def test_wrangler_runs_the_worker_only_for_the_counter_and_video_delivery() -> None:
    """Video routes serve ranges without extending the counter or running on every page."""
    assets = _wrangler()["assets"]
    media = (REPO_ROOT / "worker" / "media.mjs").read_text(encoding="utf-8")
    declared = re.search(r"export const VIDEO_ROUTES = (\[[^\n]+\])", media)
    assert declared, "video delivery must declare its Worker-first routes"
    assert assets["run_worker_first"] == [_js_const("COUNTED_PATH"), *ast.literal_eval(declared[1])]


def test_wrangler_points_at_the_worker_this_test_reads() -> None:
    """Without this, every other assertion here could be about an orphaned file."""
    main = _wrangler()["main"]
    assert (REPO_ROOT / main).resolve() == WORKER.resolve()


def test_the_bindings_the_worker_reads_are_the_ones_wrangler_declares() -> None:
    """A renamed binding is an undefined `env.X`, which is a 500 on the assets path.

    The assets binding is the load-bearing half: `env.ASSETS.fetch` is how every request
    that reaches the script gets its response, so a rename there takes the site down
    rather than merely stopping the counting.
    """
    config = _wrangler()
    source = _worker_source()

    assets_binding = config["assets"]["binding"]
    assert f"env.{assets_binding}.fetch" in source

    datasets = config["analytics_engine_datasets"]
    assert len(datasets) == 1, "a second dataset needs a second reader in metrics_snapshot.py"
    assert f"env.{datasets[0]['binding']}?.writeDataPoint" in source


def test_the_snapshot_queries_the_dataset_and_label_the_worker_writes() -> None:
    """The reader and the writer agree, or the query returns nothing and says so quietly.

    An empty result from Analytics Engine is indistinguishable from "nobody used the
    software", which is the most expensive possible way for a typo to fail.
    """
    assert _py_const("CLOUDFLARE_DATASET") == _wrangler()["analytics_engine_datasets"][0]["dataset"]
    assert _py_const("VERSION_CHECK_LABEL") == _js_const("COUNTED_LABEL")


def test_the_counter_writes_only_a_constant_label_and_a_status_code() -> None:
    """The privacy page's schema claim, asserted against the call that implements it.

    Deliberately an exact match on the normalized call rather than a search for banned
    fields: a blocklist only catches the ways of identifying a visitor that somebody
    thought of, and the point of the promise is that there are none at all. Anything
    added here should fail this test, and then either be removed or be described on the
    privacy page before the test is updated - in that order.
    """
    match = re.search(r"writeDataPoint\(\{(.*?)\}\)", _worker_source(), re.DOTALL)
    assert match, "the data point is no longer written with an inline object literal"
    normalized = " ".join(match.group(1).split())
    assert normalized == "blobs: [COUNTED_LABEL], doubles: [response.status],"


def test_the_worker_reads_nothing_from_the_request_but_its_path() -> None:
    """Belt to the previous test's braces, and it covers a different mistake.

    The exact-match above is about the data point. This is about the file: a helper that
    reads a header, or a hash computed a few lines earlier and passed in under an
    innocent name, would satisfy an equality check on the literal and still be exactly
    what the page promises does not happen.
    """
    source = _worker_source()
    for banned in (
        "request.headers",
        "request.cf",
        ".headers.get",
        "cf-connecting-ip",
        "crypto.subtle",
        "btoa(",
    ):
        assert banned not in source, f"{banned} has no place in a counter that identifies nobody"
    # One *invocation*, so the exact-match test above covers the whole of what is
    # written. Matched with the brace so the prose and the JSDoc type in this file's own
    # header do not count as calls.
    assert source.count("writeDataPoint({") == 1


def test_the_privacy_page_still_describes_the_counter() -> None:
    """A counter the page does not mention is the failure this whole change exists to avoid.

    The repository is public and the page invites readers to check it against the source,
    so the page going stale is worse than having no page: it converts an honest measure
    into a discoverable contradiction.
    """
    page = PRIVACY.read_text(encoding="utf-8")
    assert "worker/index.js" in page
    assert "version-check" in page
    # The opt-out is the part a reader most needs and the part most easily lost in an
    # edit: turning the update check off is what removes you from the count, because the
    # counted request is then never made.
    assert "update_check_enabled" in page
