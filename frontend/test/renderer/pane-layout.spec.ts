import { expect, test } from 'playwright/test'

/**
 * The pane geometry contract (`design/features/ui.md`): a terminal pane is two rows —
 * header and surface — and the voice surfaces float over the terminal rather than taking
 * a row from it. The pane's remaining height *is* the PTY's row count, so any layout
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
    panel: box('.dictation-panel'),
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

    // And the overlay is where it claims to be: inside the pane, over the top of the
    // terminal, strip above panel, never eating the terminal it is supposed to float on.
    expect(on.overlay!.x).toBeGreaterThanOrEqual(on.pane.x)
    expect(on.overlay!.x + on.overlay!.width).toBeLessThanOrEqual(on.pane.x + on.pane.width)
    expect(on.overlay!.y).toBeGreaterThanOrEqual(on.surface.y)
    expect(on.overlay!.y).toBeLessThan(on.surface.y + 40)
    expect(on.panel!.y).toBeGreaterThanOrEqual(on.strip!.y + on.strip!.height)
    expect(on.overlay!.height).toBeLessThan(on.surface.height * 0.6)
  })
}

test('the floating stack is click-through between its cards', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 760 })
  await page.goto('/pane-harness.html?overlay=1&mobile=0')
  await expect(page.locator('.dictation-panel')).toBeVisible()
  // The gap between the two cards still belongs to the terminal, or the overlay would
  // silently steal a band of clicks across the full width of the pane.
  const hits = await page.evaluate(() => {
    const strip = document.querySelector('.voice-strip')!.getBoundingClientRect()
    const panel = document.querySelector('.dictation-panel')!.getBoundingClientRect()
    const gap = document.elementFromPoint(strip.x + strip.width / 2, (strip.bottom + panel.top) / 2)
    const card = document.elementFromPoint(strip.x + strip.width / 2, strip.y + strip.height / 2)
    return { gapInTerminal: !!gap?.closest('.terminal-surface'), cardTakesPointer: !!card?.closest('.voice-strip') }
  })
  expect(hits.gapInTerminal).toBe(true)
  expect(hits.cardTakesPointer).toBe(true)
})
