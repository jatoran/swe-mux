#!/usr/bin/env python3
"""Capture named real swe-mux UI states for trailer shot planning."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "live-captures" / "exploration"
URL = "http://127.0.0.1:8765"


DRAWERS = {
    "files": "Files",
    "clipboard": "Clipboard history",
    "commands": "Commands",
    "notes": "Notes",
    "context": "Context",
    "git": "Git",
    "processes": "Processes",
    "prompts": "Prompts",
    "queue": "Queue",
    "transcript": "Transcript",
    "alerts": "Alerts",
}

SETTINGS_TABS = {
    "settings_input": "Input",
    "settings_command_rail": "Command rail",
    "settings_automation": "Automation",
    "settings_voice": "Voice",
    "settings_remote": "Remote",
    "settings_appearance": "Appearance",
}


def dismiss_tour(page: Page) -> None:
    exit_tour = page.get_by_role("button", name="Exit tutorial")
    if exit_tour.is_visible():
        exit_tour.click()
        page.wait_for_timeout(500)


def select_marketing_session(page: Page) -> None:
    session = page.get_by_role("button", name="Marketing Trailer", exact=False)
    session.click()
    page.wait_for_timeout(1_400)


def click_drawer(page: Page, label_prefix: str) -> None:
    control = page.locator(f'button[aria-label^="{label_prefix}"]')
    if control.count() != 1:
        raise RuntimeError(f"Expected one {label_prefix!r} drawer control, found {control.count()}")
    control.click()
    page.wait_for_timeout(1_000)


def apply_scenario(page: Page, scenario: str) -> None:
    if scenario == "overview":
        return
    if scenario == "terminal":
        select_marketing_session(page)
        return
    if scenario == "split_panes":
        select_marketing_session(page)
        other = page.locator("button.session-row").filter(has_text="Agent sidebar cleanup")
        if other.count() != 1:
            raise RuntimeError(f"Expected one Agent sidebar cleanup row, found {other.count()}")
        other.click(button="right")
        page.wait_for_timeout(350)
        page.evaluate(
            "window.dispatchEvent(new CustomEvent('mux:command', "
            "{detail: 'session.openSplitHorizontal'}))"
        )
        page.wait_for_timeout(1_600)
        return
    if scenario == "restore_layout":
        cleanup_tab = page.get_by_role(
            "tab", name=re.compile(r"Agent sidebar cleanup.*session tab")
        )
        if cleanup_tab.count():
            cleanup_tab.click()
            page.wait_for_timeout(300)
            page.evaluate(
                "window.dispatchEvent(new CustomEvent('mux:command', {detail: 'pane.moveTabLeft'}))"
            )
            page.wait_for_timeout(1_000)
        return
    if scenario in DRAWERS:
        select_marketing_session(page)
        click_drawer(page, DRAWERS[scenario])
        return
    if scenario == "menu":
        page.get_by_role("button", name=": menu").click()
        page.wait_for_timeout(700)
        return
    menu_scenarios = {
        "automation": "Automation",
        "settings": "All Settings",
        "usage": "Usage analytics",
        "process_fleet": "Process fleet",
        "notifications": "Notifications",
        "command_palette": "Command palette",
    }
    if scenario in menu_scenarios:
        page.get_by_role("button", name=": menu").click()
        page.wait_for_timeout(350)
        item = page.locator(".context-menu button").filter(has_text=menu_scenarios[scenario])
        if item.count() != 1:
            raise RuntimeError(
                f"Expected one {menu_scenarios[scenario]!r} menu item, found {item.count()}"
            )
        item.click()
        page.wait_for_timeout(1_100)
        return
    if scenario in SETTINGS_TABS:
        page.get_by_role("button", name=": menu").click()
        page.wait_for_timeout(350)
        page.locator(".context-menu button").filter(has_text="All Settings").click()
        page.get_by_role("dialog", name="Settings").wait_for(timeout=5_000)
        page.locator(".settings-tabs button").filter(has_text=SETTINGS_TABS[scenario]).click()
        page.wait_for_timeout(700)
        return
    if scenario == "accounts":
        account = page.locator('button[aria-label^="codex account:"]')
        if account.count() != 1:
            raise RuntimeError(f"Expected one Codex account control, found {account.count()}")
        account.click()
        page.wait_for_timeout(900)
        return
    raise ValueError(f"Unknown scenario: {scenario}")


def cleanup_scenario(page: Page, scenario: str) -> None:
    if scenario == "split_panes":
        cleanup_tab = page.get_by_role(
            "tab", name=re.compile(r"Agent sidebar cleanup.*session tab")
        )
        if cleanup_tab.count():
            cleanup_tab.click()
            page.wait_for_timeout(300)
        page.evaluate(
            "window.dispatchEvent(new CustomEvent('mux:command', {detail: 'pane.moveTabLeft'}))"
        )
        page.wait_for_timeout(1_000)


def visible_controls(page: Page) -> list[dict[str, object]]:
    return page.locator("button:visible, [role=button]:visible, input:visible").evaluate_all(
        """elements => elements.slice(0, 400).map((element, index) => ({
          index,
          tag: element.tagName.toLowerCase(),
          text: (element.innerText || element.value || '')
            .trim().replace(/\\s+/g, ' ').slice(0, 220),
          ariaLabel: element.getAttribute('aria-label'),
          title: element.getAttribute('title'),
          className: typeof element.className === 'string' ? element.className : '',
          box: (() => {
            const rect = element.getBoundingClientRect();
            return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
          })()
        }))"""
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenarios",
        nargs="*",
        default=["overview", "terminal", "menu", *DRAWERS],
    )
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for scenario in args.scenarios:
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
            )
            page = context.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(2_000)
            dismiss_tour(page)
            apply_scenario(page, scenario)
            page.screenshot(path=OUTPUT / f"{scenario}.png", full_page=False)
            state = {
                "scenario": scenario,
                "title": page.title(),
                "url": page.url,
                "controls": visible_controls(page),
            }
            (OUTPUT / f"{scenario}.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            cleanup_scenario(page, scenario)
            context.close()
            print(scenario, flush=True)
        browser.close()


if __name__ == "__main__":
    main()
