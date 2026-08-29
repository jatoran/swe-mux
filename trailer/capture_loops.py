#!/usr/bin/env python3
"""Record the site's short looping demos against the synthetic capture environment.

**Superseded as a shoot list; still the plumbing.** The committed loops are now cut
from the hero video's own takes (`capture_hero.py`, `HERO.md`), so that the page and
the film cannot drift apart, and `encode_loops.py` names a `hero-*` take for every
one of them. `capture_hero.py` imports this module's recording helpers rather than
duplicating them, and the four scenes below remain as a way to shoot a loop on its
own - but a loop shot here is not a frame of the film, which is the property the
hero takes were restructured to get.

    uv run python trailer/capture_env.py up --claude-config
    uv run --with playwright python trailer/capture_loops.py
    uv run python trailer/capture_env.py down

Same rules as `capture_site_shots.py` (see SITE_SHOTS.md): everything recorded
here is meant to be published, so it talks only to the capture daemon on 8799,
every frame comes from the synthetic fleet, and each scene's final DOM is run
through the same personal-string scan the stills use. The scan cannot read a
terminal cell grid, so the frames still get looked at before anything ships.

Each scene writes a raw `.webm` under `trailer/loops/raw/` plus a `.json` of
event timestamps (seconds from recording start), which is what the encode step
cuts against. Encoding into the committed `site/img/loop-*.mp4` files is a
separate deliberate act (`encode_loops.py`), because the cut points are chosen
by looking at the footage rather than by the script that shot it.

The fleet scene spawns real claude sessions and sends each one short, bounded,
read-only prompt - agent status is real or it is not worth showing, and a shell
session never leaves the `running` state so it cannot carry this scene. That
spend is the operator-approved kind (`SITE_SHOTS.md`, the agent-run section),
and the scene reuses the same per-session environment shape `agent-run` uses.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capture_env  # noqa: E402
from capture_site_shots import scan_for_leaks  # noqa: E402

RAW = Path(__file__).resolve().parent / "loops" / "raw"

DESKTOP_VIEW = {"width": 1920, "height": 1080}
MOBILE_VIEW = {"width": 402, "height": 874}

AGENT_FLEET = [
    ("harbor-ui", "Legend focus audit",
     "Read src/Legend.tsx and say in two sentences whether keyboard focus order matches "
     "the visual order. Do not modify or create any file."),
    ("tidepool", "Dedupe key review",
     "Read loaders/nightly.py and say in two sentences what the dedupe pass keeps and "
     "drops. Do not modify or create any file."),
    ("quill-docs", "Release notes pass",
     "Read docs/release-notes.md and suggest one improvement to its structure in two "
     "sentences. Do not modify or create any file."),
]

# ------------------------------------------------------------------- plumbing
def state() -> dict:
    return json.loads(capture_env.STATE_PATH.read_text(encoding="utf-8"))


def sessions() -> list[dict]:
    rows = capture_env.api("GET", "/api/sessions")
    if isinstance(rows, dict):
        rows = rows.get("sessions")
    assert isinstance(rows, list)
    return rows


def session_by_name(name: str) -> dict:
    for row in sessions():
        if row["name"] == name:
            return row
    raise SystemExit(f"no session named {name!r}")


def send(sid: str, data: str) -> None:
    capture_env.api("POST", f"/api/sessions/{sid}/input", {"data": data})


def wait_state(sid: str, states: set[str], timeout: float) -> dict:
    return capture_env.wait_for_session(sid, states, timeout)


def spawn_agent(project_slug: str, name: str) -> str:
    """One claude session in a synthetic project, booted and past the trust dialog.

    No per-session environment: the CLI authenticates through the daemon's
    CLAUDE_CONFIG_DIR (see `capture_env.command_agent_run` for the account-state
    prerequisite and the measured failure shapes).
    """
    projects = state()["fleet"]["projects"]
    created = capture_env.api(
        "POST",
        "/api/sessions",
        {
            "project_id": projects[project_slug],
            "backend": "claude",
            "name": name,
        },
    )
    assert isinstance(created, dict)
    sid = str(created["id"])
    capture_env.api("PATCH", f"/api/sessions/{sid}", {"name": name})
    wait_state(sid, {"idle", "awaiting", "running"}, 120)
    time.sleep(6)
    # First run in a directory raises the trust dialog whose default is "No,
    # exit"; arrow-down then Enter accepts, and is a no-op when already trusted.
    send(sid, "\x1b[B")
    time.sleep(1)
    send(sid, "\r")
    time.sleep(3)
    record = capture_env.api("GET", f"/api/sessions/{sid}")
    assert isinstance(record, dict)
    if record.get("state") in {"exited", "crashed"}:
        raise SystemExit(f"agent session {name!r} died during startup")
    return sid


def dismiss_update_banner(page: Page) -> None:
    """A daemon started before `update_check_enabled = false` still shows the
    banner it already fetched; recordings dismiss it rather than crop around it."""
    dismiss = page.get_by_role("button", name="Dismiss")
    if dismiss.count():
        dismiss.first.click()
        page.wait_for_timeout(500)


def open_workspace(page: Page, *, session: str | None = None) -> None:
    page.goto(capture_env.BASE, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("button.session-row", timeout=30_000)
    dismiss_update_banner(page)
    if session:
        page.locator("button.session-row").filter(has_text=session).first.click()
        page.wait_for_timeout(1500)


def recorded(browser: Browser, scene: str, view: dict, *, mobile: bool = False):
    # The video canvas is the viewport's CSS size: Playwright never renders the
    # page larger than the viewport into the recording, so a bigger canvas only
    # letterboxes (measured - a 2x mobile canvas came back three-quarters grey).
    context = browser.new_context(
        viewport=view,
        device_scale_factor=2 if mobile else 1,
        is_mobile=mobile,
        has_touch=mobile,
        record_video_dir=str(RAW),
        record_video_size=view,
    )
    context.add_init_script(
        "localStorage.setItem('mux.tutorial.v1','1');"
        + ("" if mobile else "localStorage.setItem('mux.drawer.width.v1','860');")
    )
    return context


def finish_scene(context, page: Page, scene: str, events: dict[str, float]) -> None:
    scan_for_leaks(page, scene)
    video = page.video
    context.close()
    assert video is not None
    target = RAW / f"{scene}.webm"
    target.unlink(missing_ok=True)
    Path(video.path()).rename(target)
    (RAW / f"{scene}.json").write_text(
        json.dumps(events, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"{scene}: {target}")
    for name, at in sorted(events.items(), key=lambda item: item[1]):
        print(f"  {at:7.2f}s {name}")


# ---------------------------------------------------------------------- scenes
def scene_fleet(browser: Browser) -> None:
    """Three real agents brought to work in parallel, status moving on its own.

    The prompts are sent through the daemon's own input route while the browser
    only watches, so the recording contains status changing with nobody typing.
    """
    agents = [(spawn_agent(slug, name), name, prompt) for slug, name, prompt in AGENT_FLEET]
    context = recorded(browser, "loop-fleet", DESKTOP_VIEW)
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
    # Hold until the first agent finishes so the edit can also show a
    # working-to-done transition, bounded so a slow turn cannot hang the shoot.
    deadline = time.time() + 150
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
    page.wait_for_timeout(4000)
    events["end"] = time.time() - start
    finish_scene(context, page, "loop-fleet", events)


def scene_restart(browser: Browser) -> None:
    """A ping keeps counting while the daemon restarts underneath it.

    The sequence numbers continuing across the reconnect are the proof; the
    edit may cut the dead middle but must keep numbers from both sides.
    """
    row = session_by_name("Ingest throughput bench")
    sid = row["id"]
    send(sid, "\x03")
    time.sleep(0.8)
    send(sid, "cls\r")
    time.sleep(1.5)
    context = recorded(browser, "loop-restart", DESKTOP_VIEW)
    page = context.new_page()
    open_workspace(page, session="Ingest throughput bench")
    events: dict[str, float] = {}
    start = time.time()
    page.wait_for_timeout(1200)
    events["ping_start"] = time.time() - start
    send(sid, "ping 127.0.0.1 -n 60\r")
    page.wait_for_timeout(4000)
    # Through the menu rather than the API, because the click IS the story: a
    # loop where the restart has no visible cause reads as a terminal running
    # ping. The item's own label carries the claim ("keep sessions").
    events["menu_open"] = time.time() - start
    page.get_by_role("button", name=": menu").click()
    page.wait_for_timeout(1100)
    # "Reload daemon (keep sessions)" lives under the Maintenance submenu,
    # which expands on *hover* - clicking the row leaves it collapsed
    # (measured: the click take recorded a menu with no submenu and fell back
    # to an invisible API restart, which is exactly the story-less frame this
    # scene exists to avoid).
    maintenance = page.locator(".context-menu button").filter(has_text="Maintenance")
    maintenance.first.hover()
    page.wait_for_timeout(1300)
    reload_item = page.locator(".context-menu button").filter(has_text="Reload daemon")
    if not reload_item.count():
        raise SystemExit("the Maintenance submenu did not expand; nothing was restarted")
    events["restart_requested"] = time.time() - start
    reload_item.first.click()
    # The daemon is gone for a while; the page shows its reconnect state. Poll
    # health out-of-band and record when it comes back.
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(2)
        try:
            capture_env.api("GET", "/api/health")
        except SystemExit:
            continue
        except OSError:
            continue
        break
    events["daemon_ready"] = time.time() - start
    page.wait_for_timeout(12_000)
    events["end"] = time.time() - start
    finish_scene(context, page, "loop-restart", events)
    # The successor daemon has a new PID; record it so `down` still stops the
    # right process instead of signalling a ghost.
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


def scene_mobile(browser: Browser) -> None:
    """The phone driving a session: navigate, pick it, run a command, read output."""
    row = session_by_name("Ingest throughput bench")
    send(row["id"], "cls\r")
    time.sleep(1.5)
    context = recorded(browser, "loop-mobile", MOBILE_VIEW, mobile=True)
    page = context.new_page()
    page.goto(capture_env.BASE, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("button.session-row", timeout=30_000)
    dismiss_update_banner(page)
    events: dict[str, float] = {}
    start = time.time()
    page.wait_for_timeout(1500)
    events["open_nav"] = time.time() - start
    page.get_by_role("button", name="Open navigation sidebar").click()
    page.wait_for_timeout(1400)
    events["pick_session"] = time.time() - start
    page.locator("button.session-row").filter(has_text="Ingest throughput bench").first.click()
    page.wait_for_timeout(2200)
    pane = page.locator(".terminal-pane.focused .xterm-screen").first
    pane.click()
    page.wait_for_timeout(600)
    events["type_command"] = time.time() - start
    page.keyboard.type("git log --oneline -4", delay=60)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2600)
    events["end"] = time.time() - start
    finish_scene(context, page, "loop-mobile", events)


def scene_land(browser: Browser) -> None:
    """The land queue taking a branch through reconcile, verify, fast-forward."""
    fleet = state()["fleet"]
    project_id = fleet["projects"]["atlas-api"]
    worktree = str(capture_env.WORKTREE_ROOT / "atlas-api-receipt-schema")
    # The Project's opt-in plus the gate: land_queue costs no tokens, so its
    # opt-in needs no provider. The gate uses the `.worktree-verify` *script*
    # convention rather than the `[worktree] verify_command` override, because
    # the approve route resolves the override against the config envelope
    # instead of its `values` and answers `not_configured` (measured
    # 2026-08-28; the script branch ignores project values and works). The
    # script is real and bounded: it bytecode-compiles the checkout's sources.
    config_path = capture_env.CODE_ROOT / "atlas-api" / ".swe-mux" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "version = 1\n\n[automations]\nland_queue = true\n",
        encoding="utf-8",
        newline="\n",
    )
    # The script goes to the worktree (what the pipeline runs) AND the project
    # root (what the landing strip *describes* - it reads the Project-wide
    # convention from the root, and with only the worktree copy present the
    # strip renders an orange "Not configured" band over a land that works).
    verify_script = "#!/usr/bin/env bash\nset -euo pipefail\npython -m compileall src tests\n"
    for target in (Path(worktree), capture_env.CODE_ROOT / "atlas-api"):
        (target / ".worktree-verify").write_text(
            verify_script, encoding="utf-8", newline="\n"
        )
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
    context = recorded(browser, "loop-land", DESKTOP_VIEW)
    page = context.new_page()
    open_workspace(page, session="Rate limit the ingest route")
    events: dict[str, float] = {}
    start = time.time()
    # Live content in the panes while the landing runs, sent through the
    # daemon's input route at the width the recording attached - the frame
    # should read as a working fleet, not as two empty prompts.
    left = session_by_name("Rate limit the ingest route")
    right = session_by_name("Receipt schema v2")
    send(left["id"], "cls\r")
    send(right["id"], "cls\r")
    time.sleep(0.8)
    send(left["id"], "git log --oneline -6\r")
    send(right["id"], "git status -sb\r")
    # Git drawer, Map view. There is no Land segment any more - Phase 14's
    # segment was retired into a compact landing strip at the head of the Map
    # (`RETIRED_DRAWER_SEGMENTS`), so the scene opens Git, confirms the Map
    # segment strip is showing, and expands the strip. The tab check keys on
    # the segment strip's own labels because they are the tab's first rendered
    # text; checking for the headings toggled the tab shut and recorded a
    # drawerless frame (measured).
    for _ in range(6):
        if page.locator(".utility-rail").count():
            page.locator(".utility-rail button").first.click()
            page.wait_for_timeout(1000)
            continue
        drawer = page.locator(".utility-drawer").last
        # The Git tab's first rendered text is its segment strip, and the
        # segment buttons' accessible names carry their long titles - so the
        # tab is recognised by the strip's leading label, not by button name.
        if drawer.inner_text().startswith("Map"):
            break
        page.locator('.utility-drawer button[aria-label^="Git"]').first.click()
        page.wait_for_timeout(1200)
    else:
        raise SystemExit("could not show the Git drawer tab")
    drawer = page.locator(".utility-drawer").last
    summary = drawer.locator("button.git-landing-summary")
    summary.wait_for(state="visible", timeout=10_000)
    if summary.get_attribute("aria-expanded") != "true":
        summary.click()
        page.wait_for_timeout(1400)
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
        page.wait_for_timeout(2000)
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
    page.wait_for_timeout(4000)
    events["end"] = time.time() - start
    finish_scene(context, page, "loop-land", events)
    print(f"  land outcome: {outcome or 'unknown'}")


SCENES = {
    "loop-fleet": scene_fleet,
    "loop-restart": scene_restart,
    "loop-mobile": scene_mobile,
    "loop-land": scene_land,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenes", nargs="*", help="Scene names; default records all four.")
    args = parser.parse_args()
    requested = args.scenes or list(SCENES)
    unknown = [name for name in requested if name not in SCENES]
    if unknown:
        parser.error(f"unknown scene(s): {', '.join(unknown)}")
    if capture_env.PORT == capture_env.LIVE_PORT:
        raise SystemExit("the capture port is the operator's port; refusing to record")
    RAW.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name in requested:
            print(f"recording {name}")
            SCENES[name](browser)
        browser.close()
    print("\nLook at the footage before encoding. The script cannot tell you what is in it.")


if __name__ == "__main__":
    main()
