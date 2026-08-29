#!/usr/bin/env python3
"""Record the hero video's six beats, in order, as one continuous workflow.

    uv run python trailer/capture_env.py up --claude-config
    uv run python trailer/capture_env.py agent-run
    uv run --with playwright python trailer/capture_hero.py
    uv run python trailer/capture_env.py down

`HERO.md` is the brief: one workflow in sixty to seventy-five seconds, not a
feature montage, with evidence and approved landing as the payoff rather than
orchestrator fan-out. This script is the recording half; `encode_hero.py` cuts
it, and `encode_loops.py` cuts the site's short loops out of the same takes so
the page and the film cannot drift apart.

Same rules as `capture_site_shots.py` and `capture_loops.py` (see
`SITE_SHOTS.md`): the capture daemon on 8799 only, the synthetic fleet only,
`scan_for_leaks` on every take's final DOM, and the frames still get looked at
because a scanner reads the DOM and a terminal is a cell grid.

The beats share state on purpose. The agents started in beat 1 are the ones
whose evidence is read in beat 4, whose worktree's branch is landed in beat 5,
and which are still alive across the reload in beat 6. Recording a beat alone is
supported for iteration, but a beat run out of order films a workflow that did
not happen.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capture_env  # noqa: E402
from capture_loops import (  # noqa: E402
    DESKTOP_VIEW,
    MOBILE_VIEW,
    RAW,
    dismiss_update_banner,
    finish_scene,
    open_workspace,
    recorded,
    send,
    session_by_name,
    sessions,
    state,
    wait_state,
)
from capture_site_shots import drawer, mobile_drawer_tab  # noqa: E402

# Beat 1. Three agents, each in a worktree of the Project it belongs to - which
# is the claim the beat makes, so the cwd is a worktree path rather than a
# Project root. `resolve_listed_cwd` accepts these because `git worktree list`
# says they are worktrees of that repository; an arbitrary directory is refused.
WORKTREE_FLEET = [
    (
        "atlas-api",
        "atlas-api-rate-limit-ingest",
        "Rate limiter review",
        "Read src/limits.py and say in two sentences what happens to an idle tenant's "
        "bucket. Do not modify or create any file.",
    ),
    (
        "harbor-ui",
        "harbor-ui-legend-focus-order",
        "Legend focus audit",
        "Read src/Legend.tsx and say in two sentences whether keyboard focus order matches "
        "the visual order. Do not modify or create any file.",
    ),
    (
        "atlas-api",
        "atlas-api-receipt-schema",
        "Receipt schema check",
        "Read src/ingest.py and say in two sentences what the receipt schema requires of a "
        "caller. Do not modify or create any file.",
    ),
]

# Beat 5 lands this one, and beat 1 put an agent in it. The through-line is the
# point: the branch that lands is the branch someone was just working on.
#
# harbor-ui rather than atlas-api, and the reason is a real refusal rather than a
# preference. atlas-api's trunk checkout carries a *seeded uncommitted change* to
# `src/limits.py` - it is what makes its `git status` pane worth photographing -
# and `rate-limit-ingest` touches the same file, so the fast-forward correctly
# refuses with "your local changes would be overwritten" (measured 2026-08-28:
# the branch reconciled and passed the gate, and then the landing step said no).
# That refusal is right, and filming it would be filming a failure. Discarding or
# stashing the seeded change to get around it would break the still shots that
# depend on it. harbor-ui's trunk is clean and `legend-focus-order` is one ahead
# and one behind, so it has something to say in both directions.
LAND_PROJECT = "harbor-ui"
LAND_WORKTREE = "harbor-ui-legend-focus-order"
LAND_SESSION = "Chart legend focus order"
LAND_SECOND_SESSION = "Design token audit"

# Real, bounded, and portable across the synthetic repositories - one of them is
# TypeScript, so a gate that assumed Python would fail there for a reason that
# has nothing to do with the branch. `--check` fails on whitespace damage and
# `compileall` on a syntax error; both are the sort of thing a gate is for, and
# both finish in under a second.
VERIFY_SCRIPT = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "git --no-pager diff --check\n"
    "python -m compileall -q src\n"
)

# Beat 6. A counter rather than a ping, because the claim is that the *same*
# process kept running and a reader can only check that against numbers. Windows
# `ping` prints identical lines, so a take built on it proves nothing to a viewer
# - it looks like a terminal that scrolled.
COUNTER_SESSION = "Ingest throughput bench"
COUNTER_COMMAND = "1..2000 | ForEach-Object { \"tick $_\"; Start-Sleep -Milliseconds 350 }"

# The stale sessions from an earlier loop shoot. Beat 1 is "three agents start",
# and three is not legible beside four idle strangers from a previous take.
STALE_AGENTS = ("Legend focus audit", "Dedupe key review", "Release notes pass")


def spawn_agent_in(project_slug: str, worktree: str, name: str) -> str:
    """One claude session, booted and past the trust dialog, inside a worktree.

    No per-session environment: the CLI authenticates through the daemon's
    `CLAUDE_CONFIG_DIR` while `USERPROFILE` stays synthetic, which is the shape
    `capture_env.command_agent_run` documents and the only one measured to work.
    """
    projects = state()["fleet"]["projects"]
    created = capture_env.api(
        "POST",
        "/api/sessions",
        {
            "project_id": projects[project_slug],
            "backend": "claude",
            "name": name,
            "cwd": str(capture_env.WORKTREE_ROOT / worktree),
        },
    )
    assert isinstance(created, dict)
    sid = str(created["id"])
    capture_env.api("PATCH", f"/api/sessions/{sid}", {"name": name})
    wait_state(sid, {"idle", "awaiting", "running"}, 120)
    time.sleep(6)
    # First run in a directory raises the trust dialog whose *default* is
    # "No, exit"; arrow-down then Enter accepts, and is a no-op in an
    # already-trusted prompt's empty composer.
    send(sid, "\x1b[B")
    time.sleep(1)
    send(sid, "\r")
    time.sleep(3)
    record = capture_env.api("GET", f"/api/sessions/{sid}")
    assert isinstance(record, dict)
    if record.get("state") in {"exited", "crashed"}:
        raise SystemExit(f"agent session {name!r} died during startup")
    return sid


def freshen_branch(worktree: Path, trunk: str = "main") -> None:
    """Make sure the branch has something to land, so the beat has something to film.

    Re-recording beat 5 is otherwise a one-shot: the first take lands the branch,
    and every take after it films an empty queue. This commits one real change,
    authored by one of the environment's invented authors (the synthetic home's
    `.gitconfig`), which is the same way `capture_env.build_repos` writes the rest
    of the history - so it stays a real repository rather than a mocked one.
    """
    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"{trunk}..HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if ahead != "0":
        return
    target = next(
        (path for path in sorted((worktree / "src").glob("*")) if path.is_file()),
        None,
    )
    if target is None:
        raise SystemExit(f"{worktree} has no src/ file to change; nothing to land")
    body = target.read_text(encoding="utf-8")
    marker = "// focus order is asserted in the tests, not inferred from the DOM\n"
    if target.suffix == ".py":
        marker = "# focus order is asserted in the tests, not inferred from the DOM\n"
    target.write_text(body.rstrip("\n") + "\n" + marker, encoding="utf-8", newline="\n")
    author, email = capture_env.AUTHORS[0]
    # The identity is passed explicitly rather than inherited. A bare `git commit`
    # here would run under the *operator's* environment and stamp their real name
    # and email into a repository the film photographs - the exact class of leak
    # the synthetic environment exists to prevent, arriving through the one door
    # `child_env` does not cover because this call is not a daemon child.
    subprocess.run(
        ["git", "add", "--", f"src/{target.name}"],
        cwd=worktree,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c", f"user.name={author}",
            "-c", f"user.email={email}",
            "commit",
            "-m", "Say where focus order is asserted",
        ],
        cwd=worktree,
        env=capture_env.child_env(),
        check=True,
        capture_output=True,
    )
    print(f"  committed one change on {worktree.name} so there is a branch to land")


def retire_stale_agents() -> None:
    for row in sessions():
        if row.get("name") in STALE_AGENTS and row.get("backend") == "claude":
            capture_env.api("DELETE", f"/api/sessions/{row['id']}")
            print(f"  retired stale agent session {row['name']!r}")


def wait_all_idle(names: list[str], timeout: float) -> None:
    """Hold until every named session has finished a turn.

    Beat 5 needs this and not only for tidiness: the land pipeline refuses to
    merge underneath a session whose live cwd is the worktree and whose state is
    `starting`/`working`/`awaiting` (`server._land_busy_sessions`), which is
    exactly the agent beat 1 put there. An agent that has answered is `idle`, and
    the refusal does not apply - so the film's own ordering is what makes the
    landing possible, rather than a special case.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        records = {row["name"]: row for row in sessions()}
        pending = [
            name
            for name in names
            if records.get(name, {}).get("state") in {"starting", "working", "awaiting"}
        ]
        if not pending:
            return
        time.sleep(3)
    raise SystemExit(f"still working after {timeout:.0f}s: {', '.join(pending)}")


# ----------------------------------------------------------------------- beats
def beat_fleet(browser: Browser) -> None:
    """Beats 1 and 2: three agents start in worktrees, then nobody is at the desk.

    Prompts go through the daemon's own input route while the browser only
    watches, so the recording carries status changing with no cursor in frame.
    The tail of this take is beat 2 - the same shot, held, while the timers run
    and the first agent turns over to done.
    """
    retire_stale_agents()
    agents = [
        (spawn_agent_in(slug, tree, name), name, prompt)
        for slug, tree, name, prompt in WORKTREE_FLEET
    ]
    context = recorded(browser, "hero-fleet", DESKTOP_VIEW)
    page = context.new_page()
    open_workspace(page, session=agents[0][1])
    events: dict[str, float] = {}
    start = time.time()
    page.wait_for_timeout(1500)
    for index, (sid, name, prompt) in enumerate(agents):
        events[f"prompt_{index}_{name}"] = time.time() - start
        send(sid, prompt)
        time.sleep(1.2)
        send(sid, "\r")
        page.wait_for_timeout(2600)
    # Hold for beat 2 until the first agent finishes, so the tail carries a
    # working-to-done transition rather than three spinners. Bounded, because a
    # slow turn must not hang the shoot.
    deadline = time.time() + 180
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        records = {row["name"]: row for row in sessions()}
        done = [
            name
            for _sid, name, _prompt in agents
            if records.get(name, {}).get("last_turn_ms") is not None
        ]
        if done:
            events[f"first_done_{done[0]}"] = time.time() - start
            break
    page.wait_for_timeout(6000)
    events["end"] = time.time() - start
    finish_scene(context, page, "hero-fleet", events)


def beat_phone(browser: Browser) -> None:
    """Beat 3: one useful interruption, on a phone, with the suppressed ones beside it.

    The panel is opened rather than a push notification filmed, because a real
    push arrives on a real device signed into a real account. What the panel
    shows is the same decision the push reports: what got through, and what was
    held back and why. A shot of only the item that got through would be making
    the opposite claim - a firehose that happened to be quiet.
    """
    context = recorded(browser, "hero-phone", MOBILE_VIEW, mobile=True)
    page = context.new_page()
    page.goto(capture_env.BASE, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("button.session-row", timeout=30_000)
    dismiss_update_banner(page)
    events: dict[str, float] = {}
    start = time.time()
    page.wait_for_timeout(1400)
    events["open_alerts"] = time.time() - start
    mobile_drawer_tab(page, "Alerts")
    page.wait_for_timeout(2600)
    # Expand the held-back digest, so the frame carries a suppressed item *and
    # its reason* rather than a count. Its control is the digest summary; if the
    # panel is not showing one there is nothing to expand and the beat still
    # reads, so this is tolerant rather than asserted.
    # The control is a "show digest" button and the reasons are a sibling span,
    # so neither a text filter for "held back" nor `:has(.attention-suppressed)`
    # finds it (both measured - the second matched nothing and the beat recorded
    # without expanding). Tolerant rather than asserted, because the panel already
    # prints the held-back count *and* each reason unexpanded: expanding is a
    # better frame, not a required one.
    digest = page.get_by_role("button", name="show digest")
    if digest.count():
        events["expand_digest"] = time.time() - start
        digest.first.click()
        page.wait_for_timeout(2400)
    events["to_session"] = time.time() - start
    # The panel has to be closed before the navigation toggle is reachable, and
    # neither obvious way works. Clicking the toggle with the panel open times
    # out against `.utility-drawer-scrim` intercepting the pointer; clicking the
    # scrim times out too, because it spans the viewport and its centre is
    # underneath the panel's own content (both measured). Pressing the tab that
    # is already showing is what closes the drawer - the same behaviour
    # `mobile_drawer_tab` loops around to avoid.
    page.locator('.utility-drawer button[aria-label^="Alerts"]').first.click()
    page.wait_for_timeout(1100)
    page.get_by_role("button", name="Open navigation sidebar").click()
    page.wait_for_timeout(1400)
    row = page.locator("button.session-row").filter(has_text=WORKTREE_FLEET[0][2])
    if not row.count():
        raise SystemExit(f"no session row for {WORKTREE_FLEET[0][2]!r}; run hero-fleet first")
    row.first.click()
    page.wait_for_timeout(3600)
    events["end"] = time.time() - start
    finish_scene(context, page, "hero-phone", events)


def beat_evidence(browser: Browser) -> None:
    """Beat 4: what an agent actually did, read off the record rather than asserted.

    Activity's Timeline segment is gated on `hasHarnessTranscript`, so this needs
    the run `capture_env.py agent-run` produces. The tab is reached in two steps
    with two different oracles for the reason `capture_site_shots.shot_desktop_insight`
    documents: the segment buttons render only while Activity is the active tab,
    and clicking an already-active tab shuts the drawer.
    """
    if not state().get("agent_session"):
        raise SystemExit(
            "beat 4 needs the real agent run behind it: `capture_env.py up --claude-config` "
            "then `capture_env.py agent-run`."
        )
    context = recorded(browser, "hero-evidence", DESKTOP_VIEW)
    page = context.new_page()
    open_workspace(page, session=capture_env.AGENT_SESSION_NAME)
    events: dict[str, float] = {}
    start = time.time()
    page.wait_for_timeout(1400)
    events["open_activity"] = time.time() - start
    headings = ("Scan Timeline", "Findings", "Change Map")
    for _ in range(6):
        if page.locator(".utility-rail").count():
            page.locator(".utility-rail button").first.click()
            page.wait_for_timeout(1000)
            continue
        if any(drawer(page).inner_text().startswith(item) for item in headings):
            break
        page.locator('.utility-drawer button[aria-label^="Activity"]').first.click()
        page.wait_for_timeout(1200)
    else:
        raise SystemExit("could not show the Activity drawer tab")
    if not drawer(page).inner_text().startswith("Scan Timeline"):
        events["pick_timeline"] = time.time() - start
        segment = drawer(page).get_by_role("button", name="Timeline", exact=True)
        if not segment.count():
            raise SystemExit(
                "the Activity tab offers no Timeline segment - the agent session has no "
                "harness transcript bound; re-check `agent-run`"
            )
        segment.first.click()
    page.wait_for_timeout(2600)
    if not drawer(page).inner_text().startswith("Scan Timeline"):
        raise SystemExit("the Activity drawer did not land on the Scan Timeline segment")
    # Read down the records the way someone checking a run would, in two unhurried
    # steps rather than one flick - a fast scroll in a muted autoplaying loop reads
    # as a glitch.
    panel = drawer(page)
    events["read_records"] = time.time() - start
    for _ in range(2):
        panel.hover()
        page.mouse.wheel(0, 320)
        page.wait_for_timeout(2200)
    page.wait_for_timeout(2600)
    events["end"] = time.time() - start
    finish_scene(context, page, "hero-evidence", events)


def beat_land(browser: Browser) -> None:
    """Beat 5: a branch through the gate someone approved, not a merge button.

    The gate is the `.worktree-verify` *script* convention rather than a
    `[worktree] verify_command` override: the approve route resolves the override
    against the config envelope instead of its `values` and answers
    `not_configured` (measured 2026-08-28). The script is `VERIFY_SCRIPT` above -
    real, bounded, and the same in every synthetic repository.
    """
    fleet = state()["fleet"]
    project_root = capture_env.CODE_ROOT / LAND_PROJECT
    project_id = fleet["projects"][LAND_PROJECT]
    worktree = str(capture_env.WORKTREE_ROOT / LAND_WORKTREE)
    config_path = project_root / ".swe-mux" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if "land_queue" not in existing:
        # Keep whatever else this Project was opted into; the automations are
        # independent and another beat may need its half.
        merged = existing if existing.startswith("version") else "version = 1\n"
        if "[automations]" in merged:
            merged = merged.replace("[automations]\n", "[automations]\nland_queue = true\n")
        else:
            merged += "\n[automations]\nland_queue = true\n"
        config_path.write_text(merged, encoding="utf-8", newline="\n")
    # The script goes to the worktree (what the pipeline runs) AND the Project
    # root (what the landing strip *describes* - it reads the Project-wide
    # convention from the root, and with only the worktree copy present the strip
    # renders an orange "Not configured" band over a land that works).
    for target in (Path(worktree), project_root):
        (target / ".worktree-verify").write_text(VERIFY_SCRIPT, encoding="utf-8", newline="\n")
    described = capture_env.api(
        "GET", f"/api/land/verify-command?project_id={project_id}&worktree_root={worktree}"
    )
    assert isinstance(described, dict)
    digest = described.get("digest")
    if not digest:
        raise SystemExit(f"verify command resolution returned no digest: {described}")
    capture_env.api(
        "POST",
        "/api/land/verify-command/approve",
        {"project_id": project_id, "digest": digest, "worktree_root": worktree},
    )
    # The agent beat 1 put in this worktree has to have answered before the merge
    # can run underneath it. See `wait_all_idle`.
    wait_all_idle([name for _slug, _tree, name, _prompt in WORKTREE_FLEET], 300)
    freshen_branch(Path(worktree))
    context = recorded(browser, "hero-land", DESKTOP_VIEW)
    page = context.new_page()
    open_workspace(page, session=LAND_SESSION)
    events: dict[str, float] = {}
    start = time.time()
    # Live content in the panes, sent through the daemon's input route at the
    # width the recording attached: the frame should read as a working fleet
    # rather than two empty prompts.
    left = session_by_name(LAND_SESSION)
    right = session_by_name(LAND_SECOND_SESSION)
    send(left["id"], "cls\r")
    send(right["id"], "cls\r")
    time.sleep(0.8)
    send(left["id"], "git log --oneline -6\r")
    send(right["id"], "git status -sb\r")
    # Phase 14's Land segment was retired into a compact landing strip at the head
    # of the Git tab's Map (`RETIRED_DRAWER_SEGMENTS`), so the tab is recognised by
    # the segment strip's leading label - which is the tab's first rendered text.
    # Checking for the headings instead toggled the tab shut and recorded a
    # drawerless frame (measured).
    for _ in range(6):
        if page.locator(".utility-rail").count():
            page.locator(".utility-rail button").first.click()
            page.wait_for_timeout(1000)
            continue
        if drawer(page).inner_text().startswith("Map"):
            break
        page.locator('.utility-drawer button[aria-label^="Git"]').first.click()
        page.wait_for_timeout(1200)
    else:
        raise SystemExit("could not show the Git drawer tab")
    summary = drawer(page).locator("button.git-landing-summary")
    summary.wait_for(state="visible", timeout=10_000)
    if summary.get_attribute("aria-expanded") != "true":
        summary.click()
        page.wait_for_timeout(1400)
    page.wait_for_timeout(1600)
    events["request_land"] = time.time() - start
    row = capture_env.api(
        "POST",
        "/api/land",
        {"project_id": project_id, "worktree_root": worktree, "kind": "land"},
    )
    assert isinstance(row, dict)
    request_id = str(row.get("id") or "")
    deadline = time.time() + 240
    outcome = ""
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        status = capture_env.api("GET", f"/api/land?project_id={project_id}")
        assert isinstance(status, dict)
        rows = status.get("requests") or status.get("rows") or []
        mine = next((item for item in rows if str(item.get("id")) == request_id), None)
        if mine is None:
            continue
        current = str(mine.get("state") or mine.get("status") or "")
        if current and f"state_{current}" not in events:
            events[f"state_{current}"] = time.time() - start
        if current in {"landed", "failed", "conflict", "refused", "done", "error"}:
            outcome = current
            break
    # The strip is the last thing to know. The API reports `landed` several
    # seconds before the Git Map redraws with "1 LANDED RECENTLY" and the
    # branch row's landed timestamp - a five-second tail put the payoff in the
    # final frame and a half of the take, which is not enough to dwell on
    # (measured). Hold long enough to land the shot as well as the branch.
    page.wait_for_timeout(14_000)
    events["end"] = time.time() - start
    finish_scene(context, page, "hero-land", events)
    print(f"  land outcome: {outcome or 'unknown'}")
    if outcome not in {"landed", "done"}:
        print("  NOTE: the beat records whatever happened. Re-record after fixing it.")


def beat_reload(browser: Browser) -> None:
    """Beat 6: the daemon is replaced and the sessions do not notice.

    Through the menu rather than the API, because the click *is* the story: a
    reload with no visible cause reads as a terminal that scrolled, and the menu
    item's own label carries the claim ("keep sessions"). The counter is what
    makes it checkable - the cut removes the downtime, so the numbers on either
    side of it have to be far enough apart to be read as continuous.
    """
    row = session_by_name(COUNTER_SESSION)
    sid = row["id"]
    send(sid, "\x03")
    time.sleep(0.8)
    send(sid, "cls\r")
    time.sleep(1.5)
    context = recorded(browser, "hero-reload", DESKTOP_VIEW)
    page = context.new_page()
    open_workspace(page, session=COUNTER_SESSION)
    events: dict[str, float] = {}
    start = time.time()
    page.wait_for_timeout(1200)
    events["counter_start"] = time.time() - start
    send(sid, COUNTER_COMMAND + "\r")
    page.wait_for_timeout(6000)
    events["menu_open"] = time.time() - start
    page.get_by_role("button", name=": menu").click()
    page.wait_for_timeout(1100)
    # "Reload daemon (keep sessions)" lives under the Maintenance submenu, which
    # expands on *hover* - clicking the row leaves it collapsed (measured: the
    # click take recorded a menu with no submenu and fell back to an invisible
    # API restart, which is the story-less frame this beat exists to avoid).
    maintenance = page.locator(".context-menu button").filter(has_text="Maintenance")
    maintenance.first.hover()
    page.wait_for_timeout(1300)
    reload_item = page.locator(".context-menu button").filter(has_text="Reload daemon")
    if not reload_item.count():
        raise SystemExit("the Maintenance submenu did not expand; nothing was reloaded")
    events["reload_requested"] = time.time() - start
    reload_item.first.click()
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(2)
        try:
            capture_env.api("GET", "/api/health")
        except (SystemExit, OSError):
            continue
        break
    events["daemon_ready"] = time.time() - start
    page.wait_for_timeout(14_000)
    events["end"] = time.time() - start
    finish_scene(context, page, "hero-reload", events)
    # The successor daemon has a new PID; record it so `down` still stops the
    # right process rather than signalling a ghost.
    import psutil

    for connection in psutil.net_connections(kind="tcp"):
        if (
            connection.laddr
            and connection.laddr.port == capture_env.PORT
            and connection.status == "LISTEN"
            and connection.pid
        ):
            fresh = state()
            fresh["pid"] = connection.pid
            capture_env.STATE_PATH.write_text(
                json.dumps(fresh, indent=2), encoding="utf-8", newline="\n"
            )
            print(f"  recorded successor daemon pid {connection.pid}")
            break


BEATS = {
    "hero-fleet": beat_fleet,
    "hero-phone": beat_phone,
    "hero-evidence": beat_evidence,
    "hero-land": beat_land,
    "hero-reload": beat_reload,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("beats", nargs="*", help="Beat names; default records all five, in order.")
    args = parser.parse_args()
    requested = args.beats or list(BEATS)
    unknown = [name for name in requested if name not in BEATS]
    if unknown:
        parser.error(f"unknown beat(s): {', '.join(unknown)}")
    if capture_env.PORT == capture_env.LIVE_PORT:
        raise SystemExit("the capture port is the operator's port; refusing to record")
    RAW.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name in requested:
            print(f"recording {name}")
            BEATS[name](browser)
        browser.close()
    print("\nLook at the footage before encoding. The script cannot tell you what is in it.")


if __name__ == "__main__":
    main()
