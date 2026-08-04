#!/usr/bin/env python3
"""Record real swe-mux UI interactions for the feature trailer.

The script talks only to the already-running local app. It never starts, stops, or
redeploys swe-mux. Every scene uses a fresh browser context and leaves staged queue
messages, pane layout, and voice modes as it found them.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from playwright.sync_api import Browser, Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parent
CAPTURE_ROOT = ROOT / "live-captures" / "video"
URL = "http://127.0.0.1:8765"
DEMO_QUEUE_BODY = "Summarize progress, then propose the next highest-impact step."


CURSOR_SCRIPT = r"""
() => {
  const cursor = document.createElement('div');
  cursor.id = 'trailer-cursor';
  cursor.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:22px', 'height:22px',
    'border:2px solid #baff63', 'border-radius:50%',
    'box-shadow:0 0 0 3px rgba(8,12,15,.7),0 0 18px rgba(186,255,99,.65)',
    'pointer-events:none', 'z-index:2147483647',
    'transform:translate(-40px,-40px)', 'transition:width .12s,height .12s',
  ].join(';');
  document.body.appendChild(cursor);
  document.addEventListener('pointermove', event => {
    cursor.style.transform = `translate(${event.clientX - 11}px,${event.clientY - 11}px)`;
  }, true);
  document.addEventListener('pointerdown', () => {
    cursor.style.width = '14px'; cursor.style.height = '14px';
  }, true);
  document.addEventListener('pointerup', () => {
    cursor.style.width = '22px'; cursor.style.height = '22px';
  }, true);
}
"""


def pause(page: Page, milliseconds: int = 900) -> None:
    page.wait_for_timeout(milliseconds)


def dismiss_tour(page: Page) -> None:
    exit_tour = page.get_by_role("button", name="Exit tutorial")
    if exit_tour.is_visible():
        exit_tour.click()
        pause(page, 500)


def install_cursor(page: Page) -> None:
    page.evaluate(CURSOR_SCRIPT)
    page.mouse.move(960, 540)


def glide_click(page: Page, locator: Locator, wait: int = 850) -> None:
    locator.wait_for(state="visible", timeout=8_000)
    box = locator.bounding_box()
    if box:
        page.mouse.move(
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
            steps=18,
        )
        pause(page, 250)
    locator.click()
    pause(page, wait)


def glide_hover(page: Page, locator: Locator, wait: int = 650) -> None:
    locator.wait_for(state="visible", timeout=8_000)
    box = locator.bounding_box()
    if box:
        page.mouse.move(
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
            steps=18,
        )
    pause(page, wait)


def select_marketing(page: Page) -> None:
    row = page.locator("button.session-row").filter(has_text="Marketing Trailer")
    if row.count() == 1:
        glide_click(page, row, 1_250)
        return
    glide_click(
        page,
        page.get_by_role("button", name="Marketing Trailer", exact=False).last,
        1_250,
    )


def drawer(page: Page, label: str, wait: int = 1_000) -> None:
    glide_click(page, page.locator(f'button[aria-label^="{label}"]').last, wait)


def menu_item(page: Page, text: str, wait: int = 1_100) -> None:
    glide_click(page, page.get_by_role("button", name=": menu"), 350)
    target = page.locator(".context-menu button").filter(has_text=text)
    if target.count() != 1:
        raise RuntimeError(f"Expected one menu item containing {text!r}, got {target.count()}")
    glide_click(page, target, wait)


def scene_workspace(page: Page) -> None:
    select_marketing(page)
    glide_hover(page, page.locator("button.session-row").filter(has_text="Shared agent docs"))
    glide_hover(page, page.locator('button[aria-label^="codex account:"]'))
    glide_click(page, page.locator('button[aria-label^="codex account:"]'), 1_600)
    page.keyboard.press("Escape")
    pause(page, 500)
    glide_hover(page, page.locator('button[aria-label^="Swe-mux owned process resources"]'))


def scene_split(page: Page) -> None:
    select_marketing(page)
    other = page.locator("button.session-row").filter(has_text="Agent sidebar cleanup")
    box = other.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20, steps=18)
    other.click(button="right")
    pause(page, 1_100)
    page.evaluate(
        "window.dispatchEvent(new CustomEvent('mux:command', "
        "{detail: 'session.openSplitHorizontal'}))"
    )
    pause(page, 2_200)
    tabs = page.locator('[role="tab"][aria-label$="session tab"]')
    if tabs.count() >= 2:
        glide_click(page, tabs.last, 900)
        glide_click(page, tabs.first, 900)
    pause(page, 1_000)
    cleanup_tab = page.get_by_role("tab", name=re.compile(r"Agent sidebar cleanup.*session tab"))
    if cleanup_tab.count():
        glide_click(page, cleanup_tab, 400)
    page.evaluate(
        "window.dispatchEvent(new CustomEvent('mux:command', {detail: 'pane.moveTabLeft'}))"
    )
    pause(page, 900)


def scene_drawers(page: Page) -> None:
    select_marketing(page)
    drawer(page, "Notes", 1_400)
    note_rows = page.locator(".utility-drawer button:visible").filter(has_text="swe-mux")
    if note_rows.count():
        glide_hover(page, note_rows.first)
    drawer(page, "Context", 1_500)
    drawer(page, "Git", 1_300)
    drawer(page, "Processes", 1_500)
    drawer(page, "Transcript", 1_400)


def scene_prompt_queue(page: Page) -> None:
    select_marketing(page)
    drawer(page, "Prompts", 900)
    learn = page.get_by_role("button", name="Learn global", exact=False)
    if learn.count():
        glide_click(page, learn, 1_300)
        clear = page.locator(".terminal-pane.focused").get_by_role(
            "button", name="clear", exact=True
        )
        if clear.count():
            glide_click(page, clear, 450)
    drawer(page, "Queue", 900)
    composer = page.locator('textarea[placeholder^="Stage a message"]')
    glide_click(page, composer, 250)
    composer.type(DEMO_QUEUE_BODY, delay=24)
    pause(page, 700)
    glide_click(page, page.get_by_role("button", name="Add draft", exact=True), 1_500)
    cancel = page.get_by_role("button", name="Cancel", exact=True)
    if cancel.count():
        glide_hover(page, cancel, 700)
        glide_click(page, cancel, 700)
    page.evaluate(
        """async body => {
          const summary = await fetch('/api/queue').then(response => response.json());
          for (const target of summary.targets || []) {
            if (!target.pending) continue;
            const query = new URLSearchParams({target_session_id: target.target_session_id});
            const view = await fetch(`/api/queue/messages?${query}`)
              .then(response => response.json());
            for (const message of view.messages || []) {
              if (message.state !== 'draft' || message.body !== body) continue;
              await fetch(`/api/queue/messages/${message.id}/cancel`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({kind: 'cancelled'}),
              });
            }
          }
        }""",
        DEMO_QUEUE_BODY,
    )
    pause(page, 500)


def scene_automation(page: Page) -> None:
    menu_item(page, "Automation", 1_300)
    for label in ("attend", "review", "configure"):
        button = page.get_by_role("button", name=label, exact=True)
        if button.count() and button.is_visible():
            glide_click(page, button, 1_400)


def scene_usage(page: Page) -> None:
    menu_item(page, "Usage analytics", 1_300)
    for label in ("quota + resets", "tools + skills", "context + compaction"):
        button = page.get_by_role("button", name=label, exact=True)
        if button.count():
            glide_click(page, button, 1_250)


def open_settings(page: Page) -> None:
    menu_item(page, "All Settings", 700)
    page.get_by_role("dialog", name="Settings").wait_for(timeout=8_000)


def scene_customization(page: Page) -> None:
    open_settings(page)
    for label in ("Command rail", "Voice", "Appearance", "Remote"):
        tab = page.locator(".settings-tabs button").filter(has_text=label)
        glide_click(page, tab, 1_500)


def scene_voice(page: Page) -> None:
    select_marketing(page)
    tts = page.locator("button.voice-chip")
    glide_click(page, tts, 1_400)
    player = page.locator(".voice-strip button:visible")
    if player.count():
        glide_hover(page, player.first, 800)
    glide_click(page, tts, 1_200)
    talk = page.locator("button.conversation-chip")
    glide_click(page, talk, 1_800)
    if talk.get_attribute("aria-pressed") == "true":
        glide_click(page, talk, 700)
    # Two TTS clicks moved off -> on-demand -> auto. One more restores off.
    glide_click(page, tts, 650)


def scene_process_fleet(page: Page) -> None:
    menu_item(page, "Process fleet", 1_600)
    previews = page.get_by_role("button", name="Open preview", exact=True)
    if previews.count():
        glide_hover(page, previews.first, 1_000)
    rows = page.locator(".process-fleet button:visible")
    if rows.count():
        glide_hover(page, rows.last, 900)


def scene_palette(page: Page) -> None:
    menu_item(page, "Command palette", 800)
    search = page.locator('.command-palette input, input[placeholder="Type a command…"]')
    glide_click(page, search, 200)
    search.type("broadcast", delay=70)
    pause(page, 1_500)
    search.fill("voice")
    pause(page, 1_600)
    search.fill("split")
    pause(page, 1_500)


def scene_mobile(page: Page) -> None:
    glide_click(page, page.get_by_role("button", name="Open navigation sidebar"), 850)
    row = page.locator("button.session-row").filter(has_text="Marketing Trailer")
    row.scroll_into_view_if_needed()
    box = row.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20, steps=18)
    row.click(force=True)
    pause(page, 1_600)
    side_panel = page.locator('button[aria-label^="Open side panel"]')
    glide_click(page, side_panel, 1_200)
    notes = page.locator('button[aria-label^="Notes"]')
    if notes.count():
        glide_click(page, notes.last, 1_200)
    queue = page.locator('button[aria-label^="Queue"]')
    if queue.count():
        glide_click(page, queue.last, 1_200)
    close = page.get_by_role("button", name="Close panel")
    if close.count():
        glide_click(page, close.last, 700)
    tts = page.locator("button.voice-chip")
    if tts.count():
        glide_hover(page, tts, 700)
    rail = page.get_by_role("button", name="Configure command rail")
    if rail.count():
        glide_hover(page, rail, 900)


SCENES = [
    ("01_workspace_status", scene_workspace, False),
    ("02_split_panes", scene_split, False),
    ("03_drawers_notes", scene_drawers, False),
    ("04_prompt_queue", scene_prompt_queue, False),
    ("05_automation", scene_automation, False),
    ("06_usage_status", scene_usage, False),
    ("07_customization", scene_customization, False),
    ("08_voice_speech", scene_voice, False),
    ("09_process_fleet", scene_process_fleet, False),
    ("10_command_palette", scene_palette, False),
    ("11_mobile", scene_mobile, True),
]


def record_scene(
    browser: Browser,
    output: Path,
    name: str,
    action,
    mobile: bool,
) -> dict[str, object]:
    viewport = {"width": 430, "height": 932} if mobile else {"width": 1920, "height": 1080}
    video_size = viewport
    context = browser.new_context(
        viewport=viewport,
        device_scale_factor=1,
        record_video_dir=output,
        record_video_size=video_size,
        permissions=["microphone"],
    )
    page = context.new_page()
    started = time.time()
    page.goto(URL, wait_until="domcontentloaded", timeout=20_000)
    pause(page, 2_000)
    dismiss_tour(page)
    install_cursor(page)
    pause(page, 500)
    action_start = time.time() - started
    action(page)
    pause(page, 600)
    video = page.video
    context.close()
    if video is None:
        raise RuntimeError(f"Playwright did not produce a video for {name}")
    source = Path(video.path())
    target = output / f"{name}.webm"
    source.rename(target)
    return {
        "name": name,
        "path": target.name,
        "mobile": mobile,
        "viewport": viewport,
        "action_start": round(action_start, 3),
        "duration_wall": round(time.time() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenes",
        nargs="*",
        help="Optional scene names. The default records the complete set.",
    )
    args = parser.parse_args()
    known = {name: (action, mobile) for name, action, mobile in SCENES}
    requested = args.scenes or list(known)
    unknown = [name for name in requested if name not in known]
    if unknown:
        parser.error(f"unknown scene(s): {', '.join(unknown)}")

    run_id = time.strftime("%Y%m%d-%H%M%S")
    output = CAPTURE_ROOT / run_id
    output.mkdir(parents=True, exist_ok=False)
    manifest: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        )
        for name in requested:
            action, mobile = known[name]
            print(f"recording {name}", flush=True)
            manifest.append(record_scene(browser, output, name, action, mobile))
        browser.close()

    payload = {"run_id": run_id, "url": URL, "scenes": manifest}
    (output / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(output, flush=True)


if __name__ == "__main__":
    main()
