import { expect, test } from 'playwright/test'

/**
 * The pane geometry contract (`design/features/ui.md`): a terminal pane is two rows —
 * header and surface - and pane voice playback floats over the terminal while dictation
 * lives at app level. The pane's remaining height *is* the PTY's row count, so any layout
 * change that moves or resizes `.terminal-surface` resizes a live agent's terminal and
 * makes its TUI reflow; the damage outlives the overlay because the reflowed scrollback
 * does not come back when it closes.
 *
 * Both regressions this pins were pure CSS, invisible to tsc and to the unit suite:
 * a template that declared one track too few (the surface fell into an implicit `auto`
 * row, leaving the real `1fr` track as dead black space), and a shared row with no
 * `grid-column`, which auto-placed the surface into an implicit second column and gave
 * the terminal less than half the pane's width.
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
    host: box('.terminal-host')!, overlay: box('.voice-overlay'), strip: box('.voice-strip'),
    panel: box('.dictation-panel'), anchor: box('.voice-overlay-anchor'),
  }
}

for (const viewport of [{ name: 'desktop', width: 1200, height: 760, mobile: 0 }, { name: 'mobile', width: 390, height: 780, mobile: 1 }]) {
  test(`voice surfaces float without moving the terminal on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })

    await page.goto(`/pane-harness.html?overlay=0&mobile=${viewport.mobile}`)
    await expect(page.locator('.terminal-surface')).toBeVisible()
    const off = await page.evaluate(bounds)

    await page.goto(`/pane-harness.html?overlay=1&mobile=${viewport.mobile}`)
    await expect(page.locator('.dictation-panel')).toBeVisible()
    const on = await page.evaluate(bounds)

    // The entire point of floating: the terminal is untouched while both surfaces are up.
    expect(on.surface).toEqual(off.surface)
    expect(on.host).toEqual(off.host)

    // The pane is one column and two rows, and the surface owns all of both.
    expect(on.surface.width).toBe(on.pane.width)
    expect(on.surface.x).toBe(on.pane.x)
    expect(on.surface.y).toBe(on.bar.y + on.bar.height)
    expect(on.surface.height).toBe(on.pane.height - on.bar.height)

    // Both voice surfaces stay in the focused pane's floating stack.
    expect(on.overlay!.x).toBeGreaterThanOrEqual(on.pane.x)
    expect(on.overlay!.x + on.overlay!.width).toBeLessThanOrEqual(on.pane.x + on.pane.width)
    expect(on.overlay!.y).toBeGreaterThanOrEqual(on.surface.y)
    expect(on.overlay!.y).toBeLessThan(on.surface.y + 40)
    expect(on.overlay!.height).toBeLessThan(on.surface.height * 0.6)
    expect(on.panel!.x).toBeGreaterThanOrEqual(on.overlay!.x)
    expect(on.panel!.x + on.panel!.width).toBeLessThanOrEqual(on.overlay!.x + on.overlay!.width)
    expect(on.panel!.y).toBeGreaterThanOrEqual(on.strip!.y + on.strip!.height + 4)
    expect(on.panel!.y + on.panel!.height).toBeLessThan(on.surface.y + on.surface.height)
  })
}

test('the pane-local dictation layer stays below modal overlays', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/pane-harness.html?overlay=1&mobile=0')
  await expect(page.locator('.dictation-panel')).toBeVisible()
  const bands = await page.evaluate(() => {
    const layer = document.querySelector<HTMLElement>('.voice-overlay-anchor')!
    const modal = document.createElement('div')
    modal.className = 'palette-layer'
    document.body.append(modal)
    const result = {
      conversation: Number(getComputedStyle(layer).zIndex),
      modal: Number(getComputedStyle(modal).zIndex),
    }
    modal.remove()
    return result
  })
  expect(bands.conversation).toBeLessThan(bands.modal)
})
