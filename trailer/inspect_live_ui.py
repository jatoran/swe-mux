#!/usr/bin/env python3
"""Capture read-only desktop and mobile inventories of the live swe-mux UI."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "live-captures" / "inspection"
URL = "http://127.0.0.1:8765"


def visible_controls(page: Page) -> list[dict[str, object]]:
    return page.locator("button:visible, [role=button]:visible, input:visible").evaluate_all(
        """elements => elements.slice(0, 300).map((element, index) => ({
          index,
          tag: element.tagName.toLowerCase(),
          text: (element.innerText || element.value || '').trim().replace(/\\s+/g, ' ').slice(0, 180),
          ariaLabel: element.getAttribute('aria-label'),
          title: element.getAttribute('title'),
          className: typeof element.className === 'string' ? element.className : '',
          disabled: Boolean(element.disabled),
          box: (() => {
            const rect = element.getBoundingClientRect();
            return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
          })()
        }))"""
    )


def capture(browser: Browser, name: str, width: int, height: int, mobile: bool) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
        is_mobile=mobile,
        has_touch=mobile,
    )
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_timeout(2_500)
    exit_tour = page.get_by_role("button", name="Exit tutorial")
    if exit_tour.is_visible():
        exit_tour.click()
        page.wait_for_timeout(700)
    page.screenshot(path=OUTPUT / f"{name}.png", full_page=False)
    inventory = {
        "name": name,
        "url": page.url,
        "title": page.title(),
        "viewport": {"width": width, "height": height, "mobile": mobile},
        "controls": visible_controls(page),
    }
    (OUTPUT / f"{name}.json").write_text(
        json.dumps(inventory, indent=2), encoding="utf-8"
    )
    context.close()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        capture(browser, "desktop", 1920, 1080, False)
        capture(browser, "mobile", 430, 932, True)
        browser.close()
    print(OUTPUT)


if __name__ == "__main__":
    main()
