import { expect, test } from 'playwright/test'

/**
 * Block 7 workspace smoke: the pane geometry and mobile-composer invariants that a
 * real browser can break invisibly to `tsc` and the unit suite. Deliberately small
 * and pinned to stable selectors (`.terminal-pane`, `.pane-bar`, `.terminal-surface`,
 * `.terminal-host`, `.mobile-terminal-draft`, `.terminal-action-rail`) so it stays
 * trusted and green rather than rotting the way a broad screen-by-screen matrix does.
 *
 * The one thing that must never regress here: the pane's remaining height *is* the
 * PTY's row count, so any layout change that moves or resizes `.terminal-surface`
 * silently reflows a live agent's terminal. The mobile draft composer must overlay
 * the host, never push it.
 *
 * Focus, two-client input ownership, and drag/split live in the unit suite
 * (`modalFocus`, `inputOwnership`, `dragReorder`/`pointerDragClaim`, `layout`), which
 * is the trusted-and-green home for logic a headless browser cannot add confidence to.
 */

const bounds = () => {
  const box = (selector: string) => {
    const element = document.querySelector<HTMLElement>(selector)
    if (!element) return null
    const { x, y, width, height } = element.getBoundingClientRect()
    return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) }
  }
  return {
    pane: box('.terminal-pane')!, bar: box('.pane-bar')!, surface: box('.terminal-surface')!,
    host: box('.terminal-host')!, draft: box('.mobile-terminal-draft'), rail: box('.terminal-action-rail'),
  }
}

for (const viewport of [
  { name: 'desktop', width: 1200, height: 760, mobile: 0 },
  { name: 'mobile', width: 390, height: 780, mobile: 1 },
]) {
  test(`the terminal surface owns the pane below its bar on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto(`/pane-harness.html?overlay=0&mobile=${viewport.mobile}`)
    await expect(page.locator('.terminal-surface')).toBeVisible()
    const b = await page.evaluate(bounds)
    // One column, two rows: the surface fills the pane width and all the height
    // left below the header. A drift here resizes a live TUI.
    expect(b.surface.width).toBe(b.pane.width)
    expect(b.surface.x).toBe(b.pane.x)
    expect(b.surface.y).toBe(b.bar.y + b.bar.height)
    expect(b.surface.height).toBe(b.pane.height - b.bar.height)
  })
}

test('the mobile Draft composer overlays the host without resizing the terminal', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/pane-harness.html?overlay=0&mobile=1&draft=0')
  const off = await page.evaluate(bounds)

  await page.goto('/pane-harness.html?overlay=0&mobile=1&draft=1')
  await expect(page.locator('.mobile-terminal-draft')).toBeVisible()
  const on = await page.evaluate(bounds)

  // The terminal surface and host are byte-for-byte unchanged: the composer floats.
  expect(on.surface).toEqual(off.surface)
  expect(on.host).toEqual(off.host)
  // The draft sits inside the surface and above the action rail.
  expect(on.draft!.x).toBeGreaterThanOrEqual(on.surface.x)
  expect(on.draft!.x + on.draft!.width).toBeLessThanOrEqual(on.surface.x + on.surface.width)
  expect(on.draft!.y + on.draft!.height).toBeLessThanOrEqual(on.rail!.y)
})
