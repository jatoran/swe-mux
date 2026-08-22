import { expect, test } from 'playwright/test'

/**
 * The pane geometry contract (`design/features/ui.md`): a terminal pane is two rows —
 * header and surface — and the surface owns the whole of the second one. The pane's
 * remaining height *is* the PTY's row count, so any layout change that moves or resizes
 * `.terminal-surface` resizes a live agent's terminal and makes its TUI reflow; the damage
 * outlives whatever caused it, because the reflowed scrollback does not come back.
 *
 * Both regressions this pins were pure CSS, invisible to tsc and to the unit suite:
 * a template that declared one track too few (the surface fell into an implicit `auto`
 * row, leaving the real `1fr` track as dead black space), and a shared row with no
 * `grid-column`, which auto-placed the surface into an implicit second column and gave
 * the terminal less than half the pane's width.
 *
 * The pane now carries no voice surface at all: the conversation surface is app-level
 * chrome (`voice-dock.spec.ts`) and the read-aloud player strip is retired, so read aloud
 * is operated from the dock's `tts` tab. Its absence is asserted rather than assumed —
 * a per-session control on the pane is exactly the shape that grows back.
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
    host: box('.terminal-host')!,
    draft: box('.mobile-terminal-draft'), rail: box('.terminal-action-rail'),
  }
}

test('the mobile Draft composer overlays the host without resizing the terminal', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/pane-harness.html?mobile=1&draft=0')
  const off = await page.evaluate(bounds)
  await page.goto('/pane-harness.html?mobile=1&draft=1')
  await expect(page.locator('.mobile-terminal-draft')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Insert' })).toHaveAttribute('title', 'Insert into the agent composer without submitting')
  const on = await page.evaluate(bounds)
  expect(on.surface).toEqual(off.surface)
  expect(on.host).toEqual(off.host)
  expect(on.draft!.x).toBeGreaterThanOrEqual(on.surface.x)
  expect(on.draft!.x + on.draft!.width).toBeLessThanOrEqual(on.surface.x + on.surface.width)
  expect(on.draft!.y + on.draft!.height).toBeLessThanOrEqual(on.rail!.y)
})

for (const viewport of [{ name: 'desktop', width: 1200, height: 760, mobile: 0 }, { name: 'mobile', width: 390, height: 780, mobile: 1 }]) {
  test(`the surface owns the pane's whole second row on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })

    await page.goto(`/pane-harness.html?mobile=${viewport.mobile}`)
    await expect(page.locator('.terminal-surface')).toBeVisible()
    const on = await page.evaluate(bounds)

    // The pane is one column and two rows, and the surface owns all of both.
    expect(on.surface.width).toBe(on.pane.width)
    expect(on.surface.x).toBe(on.pane.x)
    expect(on.surface.y).toBe(on.bar.y + on.bar.height)
    expect(on.surface.height).toBe(on.pane.height - on.bar.height)
    // The host is inside the surface and shares it with the Action rail, so it is bounded
    // by the surface rather than equal to it.
    expect(on.host.y).toBeGreaterThanOrEqual(on.surface.y)
    expect(on.host.y + on.host.height).toBeLessThanOrEqual(on.surface.y + on.surface.height)

    // No voice surface is mounted in a pane, in either direction. The conversation surface
    // moved to one app-level dock, so that following focus can never remount the assistant
    // inside it; the read-aloud player strip was retired outright, because a per-session
    // control drawn once per visible pane answers the same question once per pane. Both are
    // asserted here because a pane-local voice row is the shape most likely to grow back,
    // and the way it hurts — a row taken out of the surface resizes the live PTY — is
    // exactly what the rest of this test measures.
    expect(await page.locator('.voice-dock').count()).toBe(0)
    expect(await page.locator('.voice-strip, .voice-overlay, .voice-overlay-anchor').count()).toBe(0)
  })
}

/**
 * The header names the session instead of restating its state, and the name is the one field
 * with no upper bound on its length — a generated title is a sentence. Both halves are CSS-only
 * and invisible to tsc and the unit suite: the cap is a `fit-content()` track, and the ellipsis
 * needs the `overflow:hidden` that also lets the track collapse before the control tracks do.
 */
for (const viewport of [{ name: 'desktop', width: 1200, height: 760, mobile: 0, share: 0.36 }, { name: 'mobile', width: 390, height: 780, mobile: 1, share: 0.46 }]) {
  test(`a long session name is truncated rather than crowding the pane controls on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto(`/pane-harness.html?mobile=${viewport.mobile}`)
    await expect(page.locator('.pane-title')).toBeVisible()
    const header = await page.evaluate(() => {
      const at = (selector: string) => {
        const { x, y, width, height } = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
        return { x, y, width, height, right: x + width, middle: y + height / 2 }
      }
      const title = document.querySelector<HTMLElement>('.pane-title')!
      return {
        bar: at('.pane-bar'), title: at('.pane-title'), chip: at('.pane-tools .approval-chip'), tools: at('.pane-tools'),
        transcript: at('.pane-tools .transcript-chip'),
        clipped: title.scrollWidth > title.clientWidth,
        // No status text left in the bar; the state reading it replaced lives in the tooltip.
        states: document.querySelectorAll('.pane-bar .pane-state').length,
      }
    })
    expect(header.states).toBe(0)
    expect(header.clipped).toBe(true)
    expect(header.title.width).toBeLessThanOrEqual(header.bar.width * viewport.share)
    // The tools keep their full width, and everything stays on the one row.
    expect(header.chip.width).toBeGreaterThan(0)
    expect(header.title.right).toBeLessThanOrEqual(header.tools.x)
    // `appr:` leads the group, and the group is flush with the bar's right edge — the agent
    // header has no path, so the tools hold the flexible track and right-align inside it.
    // A left-aligned group here reads as a bar that failed to lay out.
    expect(header.chip.x).toBeLessThan(header.transcript.x)
    expect(header.tools.right).toBeLessThanOrEqual(header.bar.right)
    expect(header.bar.right - header.tools.right).toBeLessThanOrEqual(14)
    expect(Math.round(header.title.middle)).toBe(Math.round(header.tools.middle))
  })

  /**
   * The fault marker is the pane's only report of a fault that has no other surface here, so
   * "drawn but zero-width" or "pushed past the track's cap" is the same as absent. The name is
   * the thing that must yield: it is already truncated, and the whole of it is in the tooltip.
   */
  test(`the fault marker survives beside a name too long for the bar on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.goto(`/pane-harness.html?fault=1&mobile=${viewport.mobile}`)
    const marker = page.locator('.pane-fault')
    await expect(marker).toBeVisible()
    const header = await page.evaluate(() => {
      const at = (selector: string) => {
        const { x, y, width, height } = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
        return { x, y, width, height, right: x + width, middle: y + height / 2 }
      }
      const title = document.querySelector<HTMLElement>('.pane-title')!
      return {
        bar: at('.pane-bar'), identity: at('.pane-identity'), title: at('.pane-title'),
        fault: at('.pane-fault'), chip: at('.pane-tools .approval-chip'),
        clipped: title.scrollWidth > title.clientWidth,
      }
    })
    expect(header.fault.width).toBeGreaterThan(0)
    // Inside the capped identity track, after the name, and clear of the pane tools.
    expect(header.fault.right).toBeLessThanOrEqual(header.identity.right + 1)
    expect(header.fault.x).toBeGreaterThanOrEqual(header.title.right)
    expect(header.fault.right).toBeLessThanOrEqual(header.chip.x)
    expect(header.identity.width).toBeLessThanOrEqual(header.bar.width * viewport.share)
    // The name gave up the width, not the marker, and the row did not wrap.
    expect(header.clipped).toBe(true)
    expect(Math.round(header.fault.middle)).toBe(Math.round(header.title.middle))
  })
}

/**
 * The pane's floating surfaces sit in a band below the app's modal layers, so a palette or
 * a dialog is never drawn behind the pane it was opened from. With the read-aloud strip
 * retired the Draft composer is the pane's only float, and it inherits the rule.
 */
test('a pane-local float stays below modal overlays', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 })
  await page.goto('/pane-harness.html?mobile=1&draft=1')
  await expect(page.locator('.mobile-terminal-draft')).toBeVisible()
  const bands = await page.evaluate(() => {
    const layer = document.querySelector<HTMLElement>('.mobile-terminal-draft')!
    const modal = document.createElement('div')
    modal.className = 'palette-layer'
    document.body.append(modal)
    const result = {
      paneFloat: Number(getComputedStyle(layer).zIndex),
      modal: Number(getComputedStyle(modal).zIndex),
    }
    modal.remove()
    return result
  })
  expect(bands.paneFloat).toBeLessThan(bands.modal)
})
