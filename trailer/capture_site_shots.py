#!/usr/bin/env python3
"""Photograph the capture environment into the nine screenshot slots `site/img/` owns.

    uv run --with playwright --with pillow python trailer/capture_env.py up
    uv run --with playwright --with pillow python trailer/capture_site_shots.py
    uv run --with playwright --with pillow python trailer/capture_env.py down

It talks only to the capture daemon on 8799 (`capture_env.PORT`), never to the
operator's daemon on 8765, and it refuses to start if the two are the same port.

Eight of the nine are captured. `BLOCKED` says which one is not and why, and the
default run prints that reason rather than quietly producing eight files. The full
account, including how to re-record after a UI change and the six things about
this UI that look like they should work and do not, is `SITE_SHOTS.md`.

Three things about the geometry, because each of them is load-bearing and none is
obvious from the numbers.

**The output sizes are not a preference.** `site/index.html` gives every image an
explicit `width`/`height`, and `site/tools/check.mjs` asserts the page does not
overflow at several widths, so a shot delivered at a different aspect ratio is a
layout regression rather than a different-looking picture. The two shapes are
2100x1275 (desktop, 28:17) and 1206x2622 (mobile, 201:437), taken from
`site/tools/placeholders.py`, which is where that table is maintained.

**Nothing is ever upscaled.** Every shot renders at more device pixels than the
slot it fills and is downscaled into it; `finish` refuses to enlarge rather than
shipping a soft image. That is also why the hero and the panel crops use
*different* geometry (see `HERO_VIEWPORT` and `PANEL_VIEWPORT`): a panel photographed
at the hero's viewport is a small panel in a large empty frame.

**Mobile needs no resampling at all.** 402x874 at `device_scale_factor=3` is
1206x2622 exactly - the real geometry of a current phone, not a contrivance - so
the mobile shots are the browser's own pixels.

Every shot is written through `write_webp`, which encodes losslessly and falls
back to high-quality lossy only when the lossless file would be heavier than
`MAX_BYTES`. A screenshot of a terminal is mostly flat colour and usually stays
lossless.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import Locator, Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

import capture_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "site" / "img"
RAW = Path(__file__).resolve().parent / "site-shots" / "raw"

DESKTOP = (2100, 1275)
MOBILE = (1206, 2622)
# The hero is the whole window, so it renders wide: 1400x850 at scale 2 is
# 2800x1700, a 0.75 downscale to the target.
HERO_VIEWPORT = {"width": 1400, "height": 850}
HERO_SCALE = 2
# A panel crop renders *small and dense* instead, and its numbers are chosen so
# the clip needs no resampling at all: a 700x425 CSS drawer at scale 3 is exactly
# 2100x1275, and 700/425 is exactly 2100/1275.
#
# The first attempt reused the hero's viewport and produced a legible-but-tiny
# panel adrift in an empty frame - at 1400 CSS px the Alerts panel's content is
# about 460 px tall in an 850 px window, so half the shot was background. The
# window is 900x430 rather than 700-something because the drawer cannot be the
# whole window: the collapsed sidebar and the workspace gutter need somewhere to
# be, and leaving them 200 px to the left of a 700 px drawer is what lets the clip
# start at the drawer's own left edge with no sliver of terminal in it.
#
# Below about 800 CSS px the workspace switches to its mobile projection and the
# drawer controls leave the viewport, so 900 is a floor rather than a preference.
PANEL_VIEWPORT = {"width": 900, "height": 430}
PANEL_SCALE = 3
MOBILE_VIEWPORT = {"width": 402, "height": 874}
MOBILE_SCALE = 3
MAX_BYTES = 420_000
ROUNDING = 4

# The tutorial is armed on any browser profile that has not seen it, and it opens
# a modal over the whole workspace. Marking it seen is what makes a shoot
# repeatable; it is a client-side fact, so it is set before the first navigation
# rather than clicked away afterwards.
#
# The drawer's width is seeded the same way. Its *tab* is not: seeding
# `mux.drawer.tab.v1` was measured to land on the unscoped presentation rather
# than on the Project's, so the tab is chosen through the UI instead
# (`open_drawer_tab`), which is also the only version of it that asserts.
def init_script(width: int | None = None) -> str:
    lines = ["localStorage.setItem('mux.tutorial.v1','1');"]
    if width:
        lines.append(f"localStorage.setItem('mux.drawer.width.v1','{width}');")
    return "".join(lines)


# The seed is not the rendered width: the drawer's box carries about 40 CSS px of
# tab rail beyond it, and the clip has to be at least as wide as the box or the
# panel's right edge is shaved off. Both seeds are therefore the target clip width
# minus that rail.
PANEL_WIDTH = 668
# The note editor sets its own type at a fixed size, so at the panel geometry a
# note is three enormous lines. It gets the hero's viewport and a drawer 1050 CSS
# px wide, which is exactly 2100 at scale 2 - again no resampling, and enough of
# the note in frame to show the headings, the nested list, and the checkboxes the
# brief asks for.
WIDE_PANEL_WIDTH = 2100 // HERO_SCALE - 40
MIN_WIDE_CLIP = float(2100 // HERO_SCALE)
# 638 rather than the hero's 850 because the clip is only 637.5 CSS px tall: a
# taller window renders drawer content *below* the frame, and scrolling to bring
# it in stops working - the document reaches its own end while the interesting
# part is still in the band the clip discards.
WIDE_PANEL_VIEWPORT = {"width": 1400, "height": 638}


# ------------------------------------------------------------------- plumbing
def write_webp(image: Image.Image, path: Path) -> None:
    """Encode both ways and keep the smaller file.

    Measured on the 2026-08-28 set: lossless wins on every mobile shot (flat
    colour, few gradients - lossy q80 was still 5-40% *larger*), while lossy wins
    on every desktop shot (2.5-4x smaller at q84). q84 was chosen by eye against
    the lossless originals at 2x magnification - terminal text, coloured commit
    hashes, and the dark gradients all survive it with no visible difference -
    so raising it buys bytes for nothing and lowering it was not re-verified.
    """
    import io

    path.parent.mkdir(parents=True, exist_ok=True)
    encodings: list[bytes] = []
    for options in (
        {"lossless": True, "quality": 100, "method": 6},
        {"lossless": False, "quality": 84, "method": 6},
    ):
        buffer = io.BytesIO()
        image.save(buffer, "WEBP", **options)
        encodings.append(buffer.getvalue())
    path.write_bytes(min(encodings, key=len))
    if path.stat().st_size > MAX_BYTES:
        raise SystemExit(
            f"{path.name} is {path.stat().st_size} bytes in its smaller encoding, over the "
            f"{MAX_BYTES} ceiling; reframe the shot rather than shipping it."
        )
    print(f"  wrote {path.name:24} {image.width}x{image.height} {path.stat().st_size:>7} bytes")


def finish(raw: Path, out: Path, target: tuple[int, int]) -> None:
    with Image.open(raw) as shot:
        image = shot.convert("RGB")
        if image.size != target:
            # A few pixels short is rounding, not softness: a clip whose CSS height
            # is 637.5 lands on 1274 or 1275 device pixels depending on where the
            # element sits, and refusing that would fail a shot over half a pixel.
            # Anything more than `ROUNDING` short is a framing mistake and stays a
            # hard failure.
            if image.width < target[0] - ROUNDING or image.height < target[1] - ROUNDING:
                raise SystemExit(
                    f"{raw.name} is {image.size}; upscaling to {target} would ship a soft "
                    "image. Widen the clip instead."
                )
            image = image.resize(target, Image.LANCZOS)
        write_webp(image, out)


def clip_around(
    page: Page, box: dict[str, float], aspect: float, minimum: float, pad: float = 0.0
) -> dict:
    """A clip of exactly `aspect`, centred on `box`, clamped inside the viewport.

    The panel decides where the shot looks; the aspect ratio decides its shape.
    Growing to the ratio rather than cropping to it is what keeps the claim inside
    the frame - a crop that trimmed the panel to fit would be the one shot whose
    argument had been cut off.
    """
    view = page.viewport_size
    assert view is not None
    # The height limit is applied to the *width* first rather than after it, because
    # doing it afterwards silently narrows the clip and shaves the right-hand edge
    # off the panel - which is how a Notes crop lost the drawer's own title bar
    # without anything failing.
    width = min(view["width"], view["height"] * aspect, max(box["width"] + 2 * pad, minimum))
    if width < minimum:
        raise SystemExit(
            f"a {view['width']}x{view['height']} viewport caps the clip at {width:.0f} CSS px, "
            f"under the {minimum:.0f} needed. Make the window taller."
        )
    if width < box["width"]:
        raise SystemExit(
            f"the panel is {box['width']:.0f} CSS px wide and the clip caps at {width:.0f}; "
            "narrow the drawer seed or make the window taller."
        )
    if width > box["width"] + 2 * pad:
        # The drawer never reaches the window edge - the collapsed sidebar and the
        # workspace gutter keep a strip of pane to its left - so a clip wider than
        # the drawer necessarily contains something that is not the panel. Say so
        # rather than shipping the sliver silently.
        print(
            f"  note clip is {width:.0f} CSS px against a {box['width']:.0f} px panel; "
            "widen the drawer or lower the geometry's scale to crop tighter"
        )
    height = width / aspect
    # Anchored to the panel's top-left, not centred on it. Centring put a sliver
    # of the terminal pane down the left edge of every panel crop, because the
    # drawer is against the right edge of the window and the clip is wider than
    # it; anchoring pushes that slack off the right-hand side instead, where
    # there is nothing.
    x = min(max(0.0, box["x"] - pad), view["width"] - width)
    y = min(max(0.0, box["y"]), view["height"] - height)
    return {"x": x, "y": y, "width": width, "height": height}


# Below this the clip would have to be enlarged to reach the target width.
MIN_PANEL_CLIP = DESKTOP[0] / PANEL_SCALE
DESKTOP_ASPECT = DESKTOP[0] / DESKTOP[1]


def personal_strings() -> list[str]:
    """Strings that must not appear in a shot, derived at run time rather than listed.

    A denylist written down in a public repository is itself a small disclosure,
    and a hand-maintained one goes stale the day the host changes. These come from
    the machine the shoot is running on: the operator's home directory and account
    name, and whatever identity git is configured to sign commits with. Every one
    of them is exactly what leaked from the captures these replace - a personal
    first name, a `C:\\Users\\<name>` path, an address on a commit.
    """
    found: list[str] = []
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    found.extend([str(home), home.name])
    for key in ("user.name", "user.email"):
        result = subprocess.run(
            ["git", "config", "--global", key], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            found.append(result.stdout.strip())
    # Only strings long enough to be an identifier rather than a coincidence.
    return sorted({item for item in found if len(item) >= 4})


def scan_for_leaks(page: Page, name: str) -> None:
    """Read the page the way a reader would, and refuse to keep a shot that names anyone.

    A complement to looking at the file, not a replacement for it: it cannot see a
    name rendered into a terminal cell grid the DOM does not carry, and it cannot
    judge a picture. What it can do is catch the class of leak that pulled the
    original screenshots, on every run, without anyone remembering to look.
    """
    text = page.inner_text("body")
    hits = [item for item in personal_strings() if item.lower() in text.lower()]
    if hits:
        raise SystemExit(
            f"{name} shows {len(hits)} string(s) belonging to this machine rather than to "
            "the capture environment. Nothing has been written to site/img. "
            f"First offender: {hits[0][:12]}..."
        )


def open_workspace(page: Page, *, session: str | None = None) -> None:
    page.goto(capture_env.BASE, wait_until="domcontentloaded", timeout=30_000)
    # Wait for the fleet rather than for a clock: the sidebar renders empty for a
    # moment on a cold load, and a fixed sleep photographed "Create your first
    # Project" once already.
    page.wait_for_selector("button.session-row", timeout=30_000)
    if session:
        page.locator("button.session-row").filter(has_text=session).first.click()
        page.wait_for_timeout(1200)


def drawer(page: Page) -> Locator:
    return page.locator(".utility-drawer").last


def open_drawer_tab(page: Page, label: str, expect: str | None = None) -> None:
    """Show a drawer tab, in the two steps the UI actually needs.

    Measured rather than assumed, because one step looks like it should work and
    does not: the vertical `utility-rail` is rendered *only while the drawer is
    closed*, and clicking one of its icons opens the drawer on the previously
    selected tab rather than on the icon that was clicked. So the rail is used for
    "open", and the tab is then chosen from the drawer's own strip, which does
    switch. The `heading` assertion is what keeps a silent revert from becoming a
    screenshot of the wrong panel - the failure this whole helper exists for
    produced a Notes shot in the Alerts slot.
    """
    # A tab's first rendered line is its heading, and a tab that opens on a
    # segment shows the *segment's* name (Git opens on "Map"), so the expected
    # word is stated by the caller rather than derived from the tab label.
    wanted = expect or label
    for _ in range(4):
        if page.locator(".utility-rail").count():
            page.locator(".utility-rail button").first.click()
            page.wait_for_timeout(1000)
            continue
        if drawer(page).inner_text()[: len(wanted)] == wanted:
            return
        # Clicking the tab that is already showing toggles the drawer *shut*, and
        # which tab is showing is persisted per Project on the server - so it is
        # whatever the previous shot left behind, not a constant. Hence the check
        # above, and hence a loop rather than a fixed sequence of clicks.
        page.locator(f'.utility-drawer button[aria-label^="{label}"]').first.click()
        page.wait_for_timeout(1200)
    raise SystemExit(f"could not show the {wanted!r} drawer tab")


def mobile_drawer_tab(page: Page, label: str) -> None:
    """The mobile half of `open_drawer_tab`, and it needs the same loop.

    The side panel opens on the tab the Project last showed - which is server
    state, so it is whatever a *desktop* shot left behind - and pressing the tab
    that is already showing closes the panel. Pressing "Notes" once produced a
    mobile Notes shot that was a picture of a terminal.
    """
    for _ in range(4):
        if not page.locator(".utility-drawer").count():
            page.locator('button[aria-label^="Open side panel"]').first.click()
            page.wait_for_timeout(1100)
            continue
        if drawer(page).inner_text()[: len(label)] == label:
            return
        page.locator(f'.utility-drawer button[aria-label^="{label}"]').first.click()
        page.wait_for_timeout(1200)
    raise SystemExit(f"could not show the {label!r} tab in the mobile side panel")


def collapse_sidebar(page: Page) -> None:
    """Give a panel crop the width to be a panel crop.

    The drawer cannot grow past the workspace it shares with the sidebar, so with
    the sidebar open it caps well under the clip floor and the sidebar would end
    up inside every shot that is supposed to be cropped to the feature.
    """
    collapse = page.locator('button[aria-label="Collapse sidebar"]')
    if collapse.count():
        collapse.first.click()
        page.wait_for_timeout(900)


# Real commands, run in the real PTY, against the synthetic repositories. Typed
# through the app's own terminal rather than written into the scrollback: an
# invented transcript pasted into a pane would be a mockup wearing a screenshot's
# clothes, and `git log` over invented history is neither.
#
# Each list opens with `cls`, and that is the load-bearing part rather than
# tidiness. A PTY is shared and is resized by whichever client is attached, so a
# pane written at 1400 CSS px and later re-attached at 402 reflows its scrollback
# into itself - duplicated half-lines, paths spliced through commands - and no
# later write repairs what is already in the buffer. Clearing first, at the exact
# width the shot will use, is what makes the visible screen a function of this
# run instead of of every run before it.
SCENE_COMMANDS = {
    "Rate limit the ingest route": ["cls", "git log --oneline -6", "git status -sb"],
    "Receipt schema v2": ["cls", "git diff --stat", "git branch -vv"],
}
_prepared: set[str] = set()

# The Notes drawer opens on a Project's auto-created empty note, which is a
# picture of the editor with nothing in it. The seeded note is addressed by a
# fragment of its title rather than by tab index, because the tab order depends on
# how many notes the Project happens to have.
SEEDED_NOTE_TAB = "Rate limiting"


def open_seeded_note(page: Page) -> None:
    tab = page.locator(".utility-drawer button").filter(has_text=SEEDED_NOTE_TAB)
    if tab.count():
        tab.first.click()
        page.wait_for_timeout(1100)


def prepare_terminals(page: Page) -> None:
    """Give the panes something to show, at the width they will be photographed at.

    Called from inside the shot rather than once per run, because "the width they
    will be photographed at" is a property of the shot: the hero opens a drawer,
    which narrows both panes, and typing before that produces output wrapped for a
    window that no longer exists.
    """
    for session, commands in SCENE_COMMANDS.items():
        if session in _prepared:
            continue
        page.locator("button.session-row").filter(has_text=session).first.click()
        page.wait_for_timeout(1400)
        pane = page.locator(".terminal-pane.focused .xterm-screen").first
        pane.click()
        for command in commands:
            page.keyboard.type(command, delay=18)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1400)
        _prepared.add(session)


# ---------------------------------------------------------------------- shots
def shot_desktop_workspace(page: Page, raw: Path) -> None:
    """The hero: the fleet, two panes over one Project, and a drawer with content.

    The one shot allowed to include chrome, so it is the whole viewport rather
    than a clip.
    """
    open_workspace(page)
    # The two-pane split is the Project's stored layout (`capture_env.seed_fleet`),
    # so nothing here has to arrange it; selecting the first session just puts the
    # focus ring on the left pane.
    page.locator("button.session-row").filter(has_text="Rate limit the ingest route").first.click()
    page.wait_for_timeout(1400)
    # Explicit, not inherited. The drawer's tab and open/closed state are
    # persisted per Project on the server, so "whatever the last shot left" is
    # what the hero would otherwise get - and the last shot in a full run leaves
    # it closed.
    open_drawer_tab(page, "Notes")
    open_seeded_note(page)
    prepare_terminals(page)
    page.locator("button.session-row").filter(has_text="Rate limit the ingest route").first.click()
    page.wait_for_timeout(1400)
    page.screenshot(path=str(raw))


def panel_shot(
    page: Page, raw: Path, label: str, settle_ms: int, expect: str | None = None
) -> None:
    open_workspace(page, session="Rate limit the ingest route")
    collapse_sidebar(page)
    open_drawer_tab(page, label, expect)
    page.wait_for_timeout(settle_ms)
    box = drawer(page).bounding_box()
    assert box is not None, "no utility drawer on the page"
    page.screenshot(path=str(raw), clip=clip_around(page, box, DESKTOP_ASPECT, MIN_PANEL_CLIP))


def shot_desktop_alerts(page: Page, raw: Path) -> None:
    open_workspace(page, session="Rate limit the ingest route")
    collapse_sidebar(page)
    open_drawer_tab(page, "Alerts")
    # The held-back items are one summary line until this is pressed. Expanding
    # them is what makes the shot carry the argument the brief asks for - a
    # suppressed item *and its reason* - rather than a count of suppressions.
    digest = page.get_by_role("button", name="show digest")
    if digest.count():
        digest.first.click()
    page.wait_for_timeout(1600)
    box = drawer(page).bounding_box()
    assert box is not None
    page.screenshot(path=str(raw), clip=clip_around(page, box, DESKTOP_ASPECT, MIN_PANEL_CLIP))


def shot_desktop_git(page: Page, raw: Path) -> None:
    panel_shot(page, raw, "Git", 3000, expect="Map")


def shot_desktop_insight(page: Page, raw: Path) -> None:
    panel_shot(page, raw, "Activity", 2200, expect="Findings")


def shot_desktop_notes(page: Page, raw: Path) -> None:
    open_workspace(page, session="Rate limit the ingest route")
    collapse_sidebar(page)
    open_drawer_tab(page, "Notes")
    open_seeded_note(page)
    page.wait_for_timeout(1600)
    box = drawer(page).bounding_box()
    assert box is not None
    # The brief wants headings, a nested list, *and* a checkbox row, and the note
    # is taller than the frame. Scrolling past the opening paragraph is what puts
    # all three in one shot; the top of the note is the least interesting part of
    # it, because it is the part a reader can infer from the title.
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 980)
    page.wait_for_timeout(900)
    page.screenshot(path=str(raw), clip=clip_around(page, box, DESKTOP_ASPECT, MIN_WIDE_CLIP))


MOBILE_SESSION = "Ingest throughput bench"
MOBILE_COMMAND = "git log --oneline -4"


def select_mobile_session(page: Page) -> None:
    """Put the phone's own pane behind every mobile shot.

    Through the navigation overlay rather than the tab rail: the rail scrolls
    horizontally on a phone and the third tab is off-screen, so clicking it fails
    with "element is outside of the viewport". Every mobile shot selects it, not
    just the terminal one - the panel shots leave a strip of the pane visible
    beside them, and the desktop-written scrollback reflows badly at this width.
    """
    page.get_by_role("button", name="Open navigation sidebar").click()
    page.wait_for_timeout(900)
    page.locator("button.session-row").filter(has_text=MOBILE_SESSION).first.click()
    page.wait_for_timeout(1800)


def shot_mobile_session(page: Page, raw: Path) -> None:
    """A session on a phone, with output that was produced *at phone width*.

    Deliberately a different session from the desktop shots. A PTY is shared, so a
    pane whose scrollback was written at 1400 CSS px and is then re-attached at 402
    reflows into itself - half-lines interleaved, paths spliced through commands.
    That is real behaviour and it photographs as a rendering bug, so the phone gets
    a pane of its own and types into it here, where the terminal is already the
    size the shot will show.
    """
    open_workspace(page)
    select_mobile_session(page)
    pane = page.locator(".terminal-pane.focused .xterm-screen").first
    pane.click()
    for command in ("cls", MOBILE_COMMAND):
        page.keyboard.type(command, delay=18)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
    page.screenshot(path=str(raw))


def shot_mobile_nav(page: Page, raw: Path) -> None:
    open_workspace(page)
    page.get_by_role("button", name="Open navigation sidebar").click()
    page.wait_for_timeout(1200)
    page.screenshot(path=str(raw))


def shot_mobile_notes(page: Page, raw: Path) -> None:
    open_workspace(page)
    select_mobile_session(page)
    mobile_drawer_tab(page, "Notes")
    open_seeded_note(page)
    page.wait_for_timeout(1200)
    page.screenshot(path=str(raw))


def shot_mobile_alerts(page: Page, raw: Path) -> None:
    open_workspace(page)
    select_mobile_session(page)
    mobile_drawer_tab(page, "Alerts")
    page.wait_for_timeout(1400)
    page.screenshot(path=str(raw))


GEOMETRY = {
    "hero": (HERO_VIEWPORT, HERO_SCALE),
    "panel": (PANEL_VIEWPORT, PANEL_SCALE),
    "wide-panel": (WIDE_PANEL_VIEWPORT, HERO_SCALE),
    "mobile": (MOBILE_VIEWPORT, MOBILE_SCALE),
}

# Slots this environment cannot honestly fill, and why. They are skipped by the
# default run and still reachable by naming them, so the claim below stays
# checkable rather than becoming folklore: name the slot and look at what comes
# out.
BLOCKED = {
    "desktop-insight.webp": (
        "Activity's Timeline segment is gated on `hasHarnessTranscript(backend)` and every "
        "session here is a shell, so the segment is not offered; its sibling Findings segment "
        "renders the detector opt-in screen because no detector has anything to report without "
        "an agent run. Both need a real agent CLI, which needs a provider credential - the one "
        "thing this environment exists to keep out. Unblocking it means letting the daemon read "
        "the operator's own agent credentials while its account chips stay synthetic; see "
        "SITE_SHOTS.md."
    ),
}

# name -> (action, geometry, drawer width seed)
SHOTS = [
    ("desktop-workspace.webp", shot_desktop_workspace, "hero", 560),
    ("desktop-alerts.webp", shot_desktop_alerts, "panel", PANEL_WIDTH),
    ("desktop-git.webp", shot_desktop_git, "panel", PANEL_WIDTH),
    ("desktop-insight.webp", shot_desktop_insight, "panel", PANEL_WIDTH),
    ("desktop-notes.webp", shot_desktop_notes, "wide-panel", WIDE_PANEL_WIDTH),
    ("mobile-session.webp", shot_mobile_session, "mobile", None),
    ("mobile-nav.webp", shot_mobile_nav, "mobile", None),
    ("mobile-notes.webp", shot_mobile_notes, "mobile", None),
    ("mobile-alerts.webp", shot_mobile_alerts, "mobile", None),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("only", nargs="*", help="Slot filenames. Default: all nine.")
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="Write the PNG under trailer/site-shots/raw and stop, without touching site/img.",
    )
    args = parser.parse_args()
    if capture_env.PORT == capture_env.LIVE_PORT:
        raise SystemExit("the capture port is the operator's port; refusing to shoot")
    known = {name: rest for name, *rest in SHOTS}
    requested = args.only or [name for name in known if name not in BLOCKED]
    for name, reason in BLOCKED.items():
        if name not in requested:
            print(f"skipping {name}: {reason}\n")
    unknown = [name for name in requested if name not in known]
    if unknown:
        parser.error(f"unknown slot(s): {', '.join(unknown)}")

    RAW.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name in requested:
            action, kind, width = known[name]
            viewport, scale = GEOMETRY[kind]
            context = browser.new_context(
                viewport=viewport,
                device_scale_factor=scale,
                is_mobile=kind == "mobile",
                has_touch=kind == "mobile",
            )
            context.add_init_script(init_script(width))
            page = context.new_page()
            raw = RAW / f"{Path(name).stem}.png"
            print(f"shooting {name}")
            action(page, raw)
            scan_for_leaks(page, name)
            context.close()
            if not args.raw_only:
                finish(raw, IMG / name, MOBILE if kind == "mobile" else DESKTOP)
        browser.close()
    print(f"\nraw frames: {RAW}")
    print("Look at every file before committing. The script cannot tell you what is in it.")


if __name__ == "__main__":
    main()
